#
# Gramps Web API - A RESTful API for the Gramps genealogy program
#
# Copyright (C) 2020        Christopher Horn
# Copyright (C) 2020, 2021  David Straub
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
#

"""Endpoint for up- and downloading media files."""

import json
import os
from http import HTTPStatus
from typing import Dict

from flask import Response, abort, request
from gramps.gen.db import DbTxn
from gramps.gen.errors import HandleError
from gramps.gen.lib import Media
from webargs import fields

from ...auth.const import PERM_EDIT_OBJ
from ..auth import require_permissions
from ..file import process_file
from ..media import get_media_handler
from ..util import get_db_handle, get_tree_from_jwt, use_args
from . import ProtectedResource
from .util import transaction_to_json, update_object


class MediaFileResource(ProtectedResource):
    """Resource for media files."""

    @use_args(
        {
            "download": fields.Boolean(load_default=False),
            "jwt": fields.String(required=False),
        },
        location="query",
    )
    def get(self, args: Dict, handle) -> Response:
        """Download a file."""
        db_handle = get_db_handle()
        try:
            obj = db_handle.get_media_from_handle(handle)
        except HandleError:
            abort(HTTPStatus.NOT_FOUND)
        tree = get_tree_from_jwt()
        handler = get_media_handler(tree).get_file_handler(handle)
        download = bool(args.get("download"))
        filename = os.path.basename(obj.path)
        return handler.send_file(
            etag=obj.checksum, download=download, filename=filename
        )

    @use_args(
        {
            "uploadmissing": fields.Boolean(load_default=False),
        },
        location="query",
    )
    def put(self, args: Dict, handle: str) -> Response:
        """Upload a file and update the media object."""
        require_permissions([PERM_EDIT_OBJ])
        db_handle = get_db_handle()
        try:
            obj: Media = db_handle.get_media_from_handle(handle)
        except HandleError:
            abort(HTTPStatus.NOT_FOUND)
        checksum_old = obj.checksum
        for etag in request.if_match:
            if etag != checksum_old:
                abort(412)
        mime = request.content_type
        if not mime:
            abort(HTTPStatus.NOT_ACCEPTABLE)
        checksum, f = process_file(request.stream)
        tree = get_tree_from_jwt()
        media_handler = get_media_handler(tree)
        if checksum == obj.checksum:
            file_handler = media_handler.get_file_handler(handle)
            if not args.get("uploadmissing") or file_handler.file_exists():
                # don't allow PUTting if the file didn't change
                abort(HTTPStatus.CONFLICT)
            # we're uploading a missing file!
            # use existing path
            path = obj.get_path()
            media_handler.upload_file(f, checksum, mime, path=path)
            return Response(status=200)
        if args.get("uploadmissing"):
            abort(HTTPStatus.CONFLICT)
        media_handler.upload_file(f, checksum, mime)
        obj.set_checksum(checksum)
        path = media_handler.get_default_filename(checksum, mime)
        obj.set_path(path)
        obj.set_mime_type(mime)
        db_handle_writable = get_db_handle(readonly=False)
        with DbTxn("Update media object", db_handle_writable) as trans:
            update_object(db_handle_writable, obj, trans)
            try:
                pass  # update_object(db_handle_writable, obj, trans)
            except ValueError:
                abort(400)
            trans_dict = transaction_to_json(trans)
        return Response(
            response=json.dumps(trans_dict), status=200, mimetype="application/json"
        )
