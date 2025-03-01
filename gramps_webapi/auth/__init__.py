#
# Gramps Web API - A RESTful API for the Gramps genealogy program
#
# Copyright (C) 2020-2022      David Straub
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

"""Define methods of providing authentication for users."""

import uuid
from abc import ABCMeta, abstractmethod
from contextlib import contextmanager
from typing import Any, Dict, List, Optional, Sequence, Set

import sqlalchemy as sa
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.sql.functions import coalesce

from ..const import DB_CONFIG_ALLOWED_KEYS
from .const import PERMISSIONS, ROLE_OWNER
from .passwords import hash_password, verify_password
from .sql_guid import GUID


user_db = SQLAlchemy()


def add_user(
    name: str,
    password: str,
    fullname: str = None,
    email: str = None,
    role: int = None,
    tree: str = None,
):
    """Add a user."""
    if name == "":
        raise ValueError("Username must not be empty")
    if password == "":
        raise ValueError("Password must not be empty")
    try:
        user = User(
            id=uuid.uuid4(),
            name=name,
            fullname=fullname,
            email=email,
            pwhash=hash_password(password),
            role=role,
            tree=tree,
        )
        user_db.session.add(user)  # pylint: disable=no-member
        user_db.session.commit()  # pylint: disable=no-member
    except IntegrityError as exc:
        raise ValueError("Invalid or existing user") from exc


def get_guid(name: str) -> None:
    """Get the GUID of an existing user by username."""
    query = user_db.session.query(User.id)  # pylint: disable=no-member
    user_id = query.filter_by(name=name).scalar()
    if user_id is None:
        raise ValueError(f"User {name} not found")
    return user_id


def get_name(guid: str) -> None:
    """Get the username of an existing user by GUID."""
    try:
        query = user_db.session.query(User.name)  # pylint: disable=no-member
        user_name = query.filter_by(id=guid).scalar()
    except StatementError as exc:
        raise ValueError(f"User ID {guid} not found") from exc
    if user_name is None:
        raise ValueError(f"User ID {guid} not found")
    return user_name


def get_tree(guid: str) -> Optional[str]:
    """Get the tree of an existing user by GUID."""
    try:
        query = user_db.session.query(User.tree)  # pylint: disable=no-member
        tree = query.filter_by(id=guid).scalar()
    except StatementError as exc:
        raise ValueError(f"User ID {guid} not found") from exc
    return tree


def delete_user(name: str) -> None:
    """Delete an existing user."""
    query = user_db.session.query(User)  # pylint: disable=no-member
    user = query.filter_by(name=name).scalar()
    if user is None:
        raise ValueError(f"User {name} not found")
    user_db.session.delete(user)  # pylint: disable=no-member
    user_db.session.commit()  # pylint: disable=no-member


def modify_user(
    name: str,
    name_new: str = None,
    password: str = None,
    fullname: str = None,
    email: str = None,
    role: int = None,
    tree: str = None,
) -> None:
    """Modify an existing user."""
    query = user_db.session.query(User)  # pylint: disable=no-member
    user = query.filter_by(name=name).one()
    if name_new is not None:
        user.name = name_new
    if password is not None:
        user.pwhash = hash_password(password)
    if fullname is not None:
        user.fullname = fullname
    if email is not None:
        user.email = email
    if role is not None:
        user.role = role
    if tree is not None:
        user.tree = tree
    user_db.session.commit()  # pylint: disable=no-member


def authorized(username: str, password: str) -> bool:
    """Return true if the user can be authenticated."""
    query = user_db.session.query(User)  # pylint: disable=no-member
    user = query.filter_by(name=username).scalar()
    if user is None:
        return False
    if user.role < 0:
        # users with negative roles cannot login!
        return False
    return verify_password(password=password, salt_hash=user.pwhash)


def get_pwhash(username: str) -> str:
    """Return the current hashed password."""
    query = user_db.session.query(User)  # pylint: disable=no-member
    user = query.filter_by(name=username).one()
    return user.pwhash


def _get_user_detail(user):
    return {
        "name": user.name,
        "email": user.email,
        "full_name": user.fullname,
        "role": user.role,
        "tree": user.tree,
    }


def get_user_details(username: str) -> Optional[Dict[str, Any]]:
    """Return details about a user."""
    query = user_db.session.query(User)  # pylint: disable=no-member
    user = query.filter_by(name=username).scalar()
    if user is None:
        return None
    return _get_user_detail(user)


def get_all_user_details(tree: Optional[str]) -> List[Dict[str, Any]]:
    """Return details about all users.

    If tree is None, return all users regardless of tree.
    If tree is not None, only return users of given tree.
    """
    query = user_db.session.query(User)  # pylint: disable=no-member
    if tree:
        query = query.filter(sa.or_(User.tree == tree, User.tree.is_(None)))
    users = query.all()
    return [_get_user_detail(user) for user in users]


def get_permissions(username: str) -> Set[str]:
    """Get the permissions of a given user."""
    query = user_db.session.query(User)  # pylint: disable=no-member
    user = query.filter_by(name=username).one()
    return PERMISSIONS[user.role]


def get_owner_emails(tree: str) -> List[str]:
    """Get e-mail addresses of all tree owners."""
    query = user_db.session.query(User)  # pylint: disable=no-member
    owners = query.filter_by(tree=tree, role=ROLE_OWNER).all()
    return [user.email for user in owners if user.email]


def get_number_users(
    tree: Optional[str] = None, roles: Optional[Sequence[int]] = None
) -> int:
    """Get the number of users in the database.

    Optionally, provide an iterable of numeric roles and/or a tree ID.
    """
    query = user_db.session.query(User)  # pylint: disable=no-member
    if roles is not None:
        query = query.filter(User.role.in_(roles))
    if tree is not None:
        query = query.filter_by(tree=tree)
    return query.count()


def fill_tree(tree: str) -> None:
    """Fill the tree column with a tree ID, if empty."""
    (
        user_db.session.query(User)  # pylint: disable=no-member
        .filter(coalesce(User.tree, "") == "")  # treat "" and NULL equally
        .update({User.tree: tree}, synchronize_session=False)
    )
    user_db.session.commit()  # pylint: disable=no-member


def config_get(key: str) -> Optional[str]:
    """Get a single config item."""
    query = user_db.session.query(Config)  # pylint: disable=no-member
    config = query.filter_by(key=key).scalar()
    if config is None:
        return None
    return config.value


def config_get_all() -> Dict[str, str]:
    """Get all config items as dictionary."""
    query = user_db.session.query(Config)  # pylint: disable=no-member
    configs = query.all()
    return {c.key: c.value for c in configs}


def config_set(key: str, value: str) -> None:
    """Set a config item."""
    if key not in DB_CONFIG_ALLOWED_KEYS:
        raise ValueError("Config key not allowed.")
    query = user_db.session.query(Config)  # pylint: disable=no-member
    config = query.filter_by(key=key).scalar()
    if config is None:  # does not exist, create
        config = Config(key=str(key), value=str(value))
    else:  # exists, update
        config.value = str(value)
    user_db.session.add(config)  # pylint: disable=no-member
    user_db.session.commit()  # pylint: disable=no-member


def config_delete(key: str) -> None:
    """Delete a config item."""
    query = user_db.session.query(Config)  # pylint: disable=no-member
    config = query.filter_by(key=key).scalar()
    if config is not None:
        user_db.session.delete(config)  # pylint: disable=no-member
        user_db.session.commit()  # pylint: disable=no-member


class User(user_db.Model):
    """User table class for sqlalchemy."""

    __tablename__ = "users"

    id = sa.Column(GUID, primary_key=True)
    name = sa.Column(sa.String, unique=True, nullable=False)
    email = sa.Column(sa.String, unique=True)
    fullname = sa.Column(sa.String)
    pwhash = sa.Column(sa.String, nullable=False)
    role = sa.Column(sa.Integer, default=0)
    tree = sa.Column(sa.String, index=True)

    def __repr__(self):
        """Return string representation of instance."""
        return f"<User(name='{self.name}', fullname='{self.fullname}')>"


class Config(user_db.Model):
    """Config table class for sqlalchemy."""

    __tablename__ = "configuration"

    id = sa.Column(sa.Integer, primary_key=True)
    key = sa.Column(sa.String, unique=True, nullable=False)
    value = sa.Column(sa.String)

    def __repr__(self):
        """Return string representation of instance."""
        return f"<Config(key='{self.key}', value='{self.value}')>"
