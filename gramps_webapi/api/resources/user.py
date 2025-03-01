#
# Gramps Web API - A RESTful API for the Gramps genealogy program
#
# Copyright (C) 2020      David Straub
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

"""User administration resources."""

import datetime
from gettext import gettext as _
from typing import Tuple

from flask import abort, jsonify, render_template
from flask_jwt_extended import create_access_token, get_jwt, get_jwt_identity
from webargs import fields

from ...auth import (
    add_user,
    authorized,
    delete_user,
    get_all_user_details,
    get_guid,
    get_name,
    get_number_users,
    get_pwhash,
    get_user_details,
    modify_user,
)
from ...auth.const import (
    CLAIM_LIMITED_SCOPE,
    PERM_ADD_OTHER_TREE_USER,
    PERM_ADD_USER,
    PERM_DEL_OTHER_TREE_USER,
    PERM_DEL_USER,
    PERM_EDIT_OTHER_TREE_USER,
    PERM_EDIT_OTHER_TREE_USER_ROLE,
    PERM_EDIT_OTHER_USER,
    PERM_EDIT_OWN_USER,
    PERM_EDIT_USER_ROLE,
    PERM_MAKE_ADMIN,
    PERM_VIEW_OTHER_TREE_USER,
    PERM_VIEW_OTHER_USER,
    ROLE_ADMIN,
    ROLE_DISABLED,
    ROLE_OWNER,
    ROLE_UNCONFIRMED,
    SCOPE_CONF_EMAIL,
    SCOPE_CREATE_ADMIN,
    SCOPE_RESET_PW,
)
from ..auth import has_permissions, require_permissions
from ..ratelimiter import limiter
from ..tasks import (
    AsyncResult,
    make_task_response,
    run_task,
    send_email_confirm_email,
    send_email_new_user,
    send_email_reset_password,
)
from ..util import get_tree_from_jwt, get_tree_id, use_args
from . import LimitedScopeProtectedResource, ProtectedResource, Resource


class UserChangeBase(ProtectedResource):
    """Base class for user change endpoints."""

    def prepare_edit(self, user_name: str) -> Tuple[str, bool]:
        """Cheks to do before processing the request."""
        if user_name == "-":
            require_permissions([PERM_EDIT_OWN_USER])
            user_id = get_jwt_identity()
            try:
                user_name = get_name(user_id)
            except ValueError:
                abort(401)
            other_tree = False
        else:
            try:
                user_id = get_guid(user_name)
            except ValueError():
                abort(404)
            source_tree = get_tree_from_jwt()
            destination_tree = get_tree_id(user_id)
            if source_tree == destination_tree:
                require_permissions([PERM_EDIT_OTHER_USER])
                other_tree = False
            else:
                require_permissions([PERM_EDIT_OTHER_TREE_USER])
                other_tree = True
        return user_name, other_tree


class UsersResource(ProtectedResource):
    """Resource for all users."""

    def get(self):
        """Get users' details."""
        if has_permissions([PERM_VIEW_OTHER_TREE_USER]):
            # return all users from all trees
            return jsonify(get_all_user_details(tree=None)), 200
        require_permissions([PERM_VIEW_OTHER_USER])
        tree = get_tree_from_jwt()
        # return only this tree's users
        return jsonify(get_all_user_details(tree=tree)), 200


class UserResource(UserChangeBase):
    """Resource for a single user."""

    def get(self, user_name: str):
        """Get a user's details."""
        if user_name == "-":
            # own user
            user_id = get_jwt_identity()
            try:
                user_name = get_name(user_id)
            except ValueError:
                abort(401)
        else:
            require_permissions([PERM_VIEW_OTHER_USER])
        if user_name != "_" and not has_permissions([PERM_VIEW_OTHER_TREE_USER]):
            # check if this is our tree
            try:
                user_id = get_guid(user_name)
            except ValueError:
                abort(404)
            source_tree = get_tree_from_jwt()
            destination_tree = get_tree_id(user_id)
            if source_tree != destination_tree:
                # user lives in other tree, not allowed to view
                abort(403)
        details = get_user_details(user_name)
        if details is None:
            # user does not exist
            abort(404)
        return jsonify(details), 200

    @use_args(
        {
            "email": fields.Str(required=False),
            "full_name": fields.Str(required=False),
            "role": fields.Int(required=False),
        },
        location="json",
    )
    def put(self, args, user_name: str):
        """Update a user's details."""
        user_name, other_tree = self.prepare_edit(user_name)
        if "role" in args:
            if args["role"] >= ROLE_ADMIN:
                # only admins can elevate users to admins
                require_permissions([PERM_MAKE_ADMIN])
            if other_tree:
                require_permissions([PERM_EDIT_OTHER_TREE_USER_ROLE])
            else:
                require_permissions([PERM_EDIT_USER_ROLE])
        modify_user(
            name=user_name,
            email=args.get("email"),
            fullname=args.get("full_name"),
            role=args.get("role"),
        )
        return "", 200

    @use_args(
        {
            "email": fields.Str(required=True),
            "full_name": fields.Str(required=True),
            "password": fields.Str(required=True),
            "role": fields.Int(required=True),
            "tree": fields.Str(required=False),
        },
        location="json",
    )
    def post(self, args, user_name: str):
        """Add a new user."""
        if user_name == "-":
            # Adding a new user does not make sense for "own" user
            abort(404)
        if args["role"] >= ROLE_ADMIN:
            # only admins can create new admin users
            require_permissions([PERM_MAKE_ADMIN])
        tree = get_tree_from_jwt()
        if not args.get("tree") or tree == args.get("tree"):
            require_permissions([PERM_ADD_USER])
        else:
            require_permissions([PERM_ADD_OTHER_TREE_USER])
        try:
            add_user(
                name=user_name,
                password=args["password"],
                email=args["email"],
                fullname=args["full_name"],
                role=args["role"],
                # use posting user's tree unless explicitly specified
                tree=args.get("tree") or tree,
            )
        except ValueError:
            abort(409)
        return "", 201

    def delete(self, user_name: str):
        """Delete a user."""
        if user_name == "-":
            # Deleting the own user is currently not allowed
            abort(404)
        try:
            user_id = get_guid(name=user_name)
        except ValueError:
            abort(404)  # user not found
        source_tree = get_tree_from_jwt()
        destination_tree = get_tree_id(user_id)
        if source_tree == destination_tree:
            require_permissions([PERM_DEL_USER])
        else:
            require_permissions([PERM_DEL_OTHER_TREE_USER])
        delete_user(name=user_name)
        return "", 200


class UserRegisterResource(Resource):
    """Resource for registering a new user."""

    @limiter.limit("1/second")
    @use_args(
        {
            "email": fields.Str(required=True),
            "full_name": fields.Str(required=True),
            "password": fields.Str(required=True),
            "tree": fields.Str(required=False),
        },
        location="json",
    )
    def post(self, args, user_name: str):
        """Register a new user."""
        if user_name == "-":
            # Registering a new user does not make sense for "own" user
            abort(404)
        # do not allow registration if no tree owner account exists!
        if get_number_users(tree=args.get("tree"), roles=(ROLE_OWNER,)) == 0:
            abort(405)
        try:
            add_user(
                name=user_name,
                password=args["password"],
                email=args["email"],
                fullname=args["full_name"],
                tree=args.get("tree"),
                role=ROLE_UNCONFIRMED,
            )
        except ValueError:
            abort(409)
        user_id = get_guid(name=user_name)
        token = create_access_token(
            identity=str(user_id),
            additional_claims={
                "email": args["email"],
                CLAIM_LIMITED_SCOPE: SCOPE_CONF_EMAIL,
            },
            # email has to be confirmed within 1h
            expires_delta=datetime.timedelta(hours=1),
        )
        run_task(send_email_confirm_email, email=args["email"], token=token)
        return "", 201


class UserCreateOwnerResource(LimitedScopeProtectedResource):
    """Resource for creating a site admin when the user database is empty."""

    @limiter.limit("1/second")
    @use_args(
        {
            "email": fields.Str(required=True),
            "full_name": fields.Str(required=True),
            "password": fields.Str(required=True),
            "tree": fields.Str(required=False),
        },
        location="json",
    )
    def post(self, args, user_name: str):
        """Create a user with owner permissions."""
        if user_name == "-":
            # User name - is not allowed
            abort(404)
        if get_number_users() > 0:
            # there is already a user in the user DB
            abort(405)
        claims = get_jwt()
        if claims[CLAIM_LIMITED_SCOPE] != SCOPE_CREATE_ADMIN:
            # This is a wrong token!
            abort(403)
        add_user(
            name=user_name,
            password=args["password"],
            email=args["email"],
            fullname=args["full_name"],
            tree=args.get("tree"),
            role=ROLE_ADMIN,
        )
        return "", 201


class UserChangePasswordResource(UserChangeBase):
    """Resource for changing a user password."""

    @use_args(
        {
            "old_password": fields.Str(required=True),
            "new_password": fields.Str(required=True),
        },
        location="json",
    )
    def post(self, args, user_name: str):
        """Post new password."""
        user_name, _ = self.prepare_edit(user_name)
        if len(args["new_password"]) == "":
            abort(400)
        if not authorized(user_name, args["old_password"]):
            abort(403)
        modify_user(name=user_name, password=args["new_password"])
        return "", 201


class UserTriggerResetPasswordResource(Resource):
    """Resource for obtaining a one-time JWT for password reset."""

    @limiter.limit("1/second")
    def post(self, user_name):
        """Post username to initiate the password reset."""
        if user_name == "-":
            # password reset trigger not make sense for "own" user since not logged in
            abort(404)
        details = get_user_details(user_name)
        if details is None:
            # user does not exist!
            abort(404)
        email = details["email"]
        if email is None:
            abort(404)
        user_id = get_guid(name=user_name)
        token = create_access_token(
            identity=str(user_id),
            # the hash of the existing password is stored in the token in order
            # to make sure the rest token can only be used once
            additional_claims={
                "old_hash": get_pwhash(user_name),
                CLAIM_LIMITED_SCOPE: SCOPE_RESET_PW,
            },
            # password reset has to be triggered within 1h
            expires_delta=datetime.timedelta(hours=1),
        )
        try:
            task = run_task(send_email_reset_password, email=email, token=token)
        except ValueError:
            abort(500)
        if isinstance(task, AsyncResult):
            return make_task_response(task)
        return "", 201


class UserResetPasswordResource(LimitedScopeProtectedResource):
    """Resource for resetting a user password."""

    @use_args(
        {"new_password": fields.Str(required=True)},
        location="json",
    )
    def post(self, args):
        """Post new password."""
        if args["new_password"] == "":
            abort(400)
        claims = get_jwt()
        if claims[CLAIM_LIMITED_SCOPE] != SCOPE_RESET_PW:
            # This is a wrong token!
            abort(403)
        user_id = get_jwt_identity()
        try:
            username = get_name(user_id)
        except ValueError:
            abort(401)
        # the old PW hash is stored in the reset JWT to check if the token has
        # been used already
        if claims["old_hash"] != get_pwhash(username):
            # the one-time token has been used before!
            abort(409)
        modify_user(name=username, password=args["new_password"])
        return "", 201

    def get(self):
        """Reset password form."""
        user_id = get_jwt_identity()
        try:
            username = get_name(user_id)
        except ValueError:
            abort(401)
        claims = get_jwt()
        if claims[CLAIM_LIMITED_SCOPE] != SCOPE_RESET_PW:
            # This is a wrong token!
            abort(403)
        # the old PW hash is stored in the reset JWT to check if the token has
        # been used already
        if claims["old_hash"] != get_pwhash(username):
            # the one-time token has been used before!
            return render_template("reset_password_error.html", username=username)
        return render_template("reset_password.html", username=username)


class UserConfirmEmailResource(LimitedScopeProtectedResource):
    """Resource for confirming an email address."""

    def get(self):
        """Show email confirmation dialog."""
        user_id = get_jwt_identity()
        try:
            username = get_name(user_id)
        except ValueError:
            abort(401)
        claims = get_jwt()
        if claims[CLAIM_LIMITED_SCOPE] != SCOPE_CONF_EMAIL:
            # This is a wrong token!
            abort(403)
        current_details = get_user_details(username)
        # the email is stored in the JWT
        if claims["email"] != current_details.get("email"):
            # This is a wrong token!
            abort(403)
        if current_details["role"] == ROLE_UNCONFIRMED:
            # otherwise it has been confirmed already
            modify_user(name=username, role=ROLE_DISABLED)
            tree = get_tree_from_jwt()
            run_task(
                send_email_new_user,
                username=username,
                fullname=current_details.get("full_name", ""),
                email=claims["email"],
                tree=tree,
            )
        title = _("E-mail address confirmation")
        message = _("Thank you for confirming your e-mail address.")
        return render_template("confirmation.html", title=title, message=message)
