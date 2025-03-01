#
# Gramps Web API - A RESTful API for the Gramps genealogy program
#
# Copyright (C) 2021-2023      David Straub
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.

"""Execute tasks."""

from gettext import gettext as _
from http import HTTPStatus
from typing import Any, Callable, Dict, Optional, Union

from celery import shared_task
from celery.result import AsyncResult
from flask import current_app

from ..auth import get_owner_emails
from .emails import email_confirm_email, email_new_user, email_reset_pw
from .export import prepare_options, run_export
from .report import run_report
from .resources.util import run_import
from .util import (
    get_config,
    get_db_manager,
    get_db_outside_request,
    get_search_indexer,
    send_email,
)


def run_task(task: Callable, **kwargs) -> Union[AsyncResult, Any]:
    """Send a task to the task queue or run immediately if no queue set up."""
    if not current_app.config["CELERY_CONFIG"]:
        with current_app.app_context():
            return task(**kwargs)
    return task.delay(**kwargs)


def make_task_response(task: AsyncResult):
    """Make a 202 response with the location of the task status endpoint."""
    url = f"/api/tasks/{task.id}"
    payload = {"task": {"href": url, "id": task.id}}
    return payload, HTTPStatus.ACCEPTED


@shared_task()
def send_email_reset_password(email: str, token: str):
    """Send an email for password reset."""
    base_url = get_config("BASE_URL").rstrip("/")
    body = email_reset_pw(base_url=base_url, token=token)
    subject = _("Reset your Gramps password")
    send_email(subject=subject, body=body, to=[email])


@shared_task()
def send_email_confirm_email(email: str, token: str):
    """Send an email to confirm an e-mail address."""
    base_url = get_config("BASE_URL").rstrip("/")
    body = email_confirm_email(base_url=base_url, token=token)
    subject = _("Confirm your e-mail address")
    send_email(subject=subject, body=body, to=[email])


@shared_task()
def send_email_new_user(username: str, fullname: str, email: str, tree: str):
    """Send an email to owners to notify of a new registered user."""
    base_url = get_config("BASE_URL").rstrip("/")
    body = email_new_user(
        base_url=base_url, username=username, fullname=fullname, email=email
    )
    subject = _("New registered user")
    emails = get_owner_emails(tree=tree)
    if emails:
        send_email(subject=subject, body=body, to=emails)


def _search_reindex_full(tree) -> None:
    """Rebuild the search index."""
    indexer = get_search_indexer(tree)
    db_manager = get_db_manager(tree)
    db = db_manager.get_db().db
    try:
        indexer.reindex_full(db)
    finally:
        db.close()


@shared_task()
def search_reindex_full(tree) -> None:
    """Rebuild the search index."""
    return _search_reindex_full(tree)


def _search_reindex_incremental(tree) -> None:
    """Run an incremental reindex of the search index."""
    indexer = get_search_indexer(tree)
    db_manager = get_db_manager(tree)
    db = db_manager.get_db().db
    try:
        indexer.reindex_incremental(db)
    finally:
        db.close()


@shared_task()
def search_reindex_incremental(tree) -> None:
    """Run an incremental reindex of the search index."""
    return _search_reindex_incremental(tree)


@shared_task()
def import_file(tree: str, file_name: str, extension: str, delete: bool = True):
    """Import a file."""
    db_manager = get_db_manager(tree)
    db_handle = db_manager.get_db().db
    run_import(
        db_handle=db_handle,
        file_name=file_name,
        extension=extension.lower(),
        delete=delete,
    )
    _search_reindex_incremental(tree)


@shared_task()
def export_db(
    tree: str, extension: str, options: Dict, view_private: bool
) -> Dict[str, str]:
    """Export a database."""
    db_handle = get_db_outside_request(
        tree=tree, view_private=view_private, readonly=True
    )
    prepared_options = prepare_options(db_handle, options)
    file_name, file_type = run_export(db_handle, extension, prepared_options)
    extension = file_type.lstrip(".")
    return {
        "file_name": file_name,
        "file_type": file_type,
        "url": f"/api/exporters/{extension}/file/processed/{file_name}",
    }


@shared_task()
def generate_report(
    tree: str,
    report_id: str,
    options: Dict,
    view_private: bool,
    locale: Optional[str] = None,
) -> Dict[str, str]:
    """Generate a Gramps report."""
    db_handle = get_db_outside_request(
        tree=tree, view_private=view_private, readonly=True
    )
    file_name, file_type = run_report(
        db_handle=db_handle,
        report_id=report_id,
        report_options=options,
        language=locale,
    )
    return {
        "file_name": file_name,
        "file_type": file_type,
        "url": f"/api/reports/{report_id}/file/processed/{file_name}",
    }
