#
# Gramps Web API - A RESTful API for the Gramps genealogy program
#
# Copyright (C) 2022      David Straub
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

"""Tests for rate limited endpoints."""

import unittest
from unittest.mock import patch

from time import sleep

from gramps.cli.clidbman import CLIDbManager
from gramps.gen.dbstate import DbState

from gramps_webapi.app import create_app
from gramps_webapi.auth import user_db, add_user
from gramps_webapi.auth.const import ROLE_GUEST, ROLE_OWNER
from gramps_webapi.const import ENV_CONFIG_FILE, TEST_AUTH_CONFIG


class TestRateLimits(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.name = "Test Web API"
        cls.dbman = CLIDbManager(DbState())
        _, _name = cls.dbman.create_new_db_cli(cls.name, dbid="sqlite")
        with patch.dict("os.environ", {ENV_CONFIG_FILE: TEST_AUTH_CONFIG}):
            cls.app = create_app(config={"TESTING": True, "RATELIMIT_ENABLED": True})
        cls.client = cls.app.test_client()
        with cls.app.app_context():
            user_db.create_all()
            add_user(name="user", password="123", role=ROLE_GUEST)
            add_user(name="admin", password="123", role=ROLE_OWNER)

    @classmethod
    def tearDownClass(cls):
        cls.dbman.remove_database(cls.name)

    def test_token_endpoint(self):
        rv = self.client.post(
            "/api/token/", json={"username": "user", "password": "123"}
        )
        assert rv.status_code == 200
        rv = self.client.post(
            "/api/token/", json={"username": "user", "password": "123"}
        )
        assert rv.status_code == 429
        sleep(1)
        rv = self.client.post(
            "/api/token/", json={"username": "user", "password": "123"}
        )
        assert rv.status_code == 200
