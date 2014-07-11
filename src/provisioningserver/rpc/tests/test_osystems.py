# Copyright 2014 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Tests for :py:module:`~provisioningserver.rpc.osystems`."""

from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

str = None

__metaclass__ = type
__all__ = []

from collections import Iterable

from maastesting.factory import factory
from maastesting.matchers import MockCalledOnceWith
from maastesting.testcase import MAASTestCase
from mock import sentinel
from provisioningserver.drivers.osystem import OperatingSystemRegistry
from provisioningserver.rpc import (
    exceptions,
    osystems,
    )
from provisioningserver.rpc.testing.doubles import StubOS


class TestListOperatingSystemHelpers(MAASTestCase):

    def test_gen_operating_systems_returns_dicts_for_registered_oses(self):
        # Patch in some operating systems with some randomised data. See
        # StubOS for details of the rules that are used to populate the
        # non-random elements.
        os1 = StubOS("kermit", [
            ("statler", "Statler"),
            ("waldorf", "Waldorf"),
        ])
        os2 = StubOS("fozzie", [
            ("swedish-chef", "Swedish-Chef"),
            ("beaker", "Beaker"),
        ])
        self.patch(
            osystems, "OperatingSystemRegistry",
            [(os1.name, os1), (os2.name, os2)])
        # The `releases` field in the dict returned is populated by
        # gen_operating_system_releases. That's not under test, so we
        # mock it.
        gen_operating_system_releases = self.patch(
            osystems, "gen_operating_system_releases")
        gen_operating_system_releases.return_value = sentinel.releases
        # The operating systems are yielded in name order.
        expected = [
            {
                "name": "fozzie",
                "title": "Fozzie",
                "releases": sentinel.releases,
                "default_release": "swedish-chef",
                "default_commissioning_release": "beaker",
            },
            {
                "name": "kermit",
                "title": "Kermit",
                "releases": sentinel.releases,
                "default_release": "statler",
                "default_commissioning_release": "waldorf",
            },
        ]
        observed = osystems.gen_operating_systems()
        self.assertIsInstance(observed, Iterable)
        self.assertEqual(expected, list(observed))

    def test_gen_operating_system_releases_returns_dicts_for_releases(self):
        # Use an operating system with some randomised data. See StubOS
        # for details of the rules that are used to populate the
        # non-random elements.
        osystem = StubOS("fozzie", [
            ("swedish-chef", "I Am The Swedish-Chef"),
            ("beaker", "Beaker The Phreaker"),
        ])
        expected = [
            {
                "name": "swedish-chef",
                "title": "I Am The Swedish-Chef",
                "requires_license_key": False,
                "can_commission": False,
            },
            {
                "name": "beaker",
                "title": "Beaker The Phreaker",
                "requires_license_key": True,
                "can_commission": True,
            },
        ]
        observed = osystems.gen_operating_system_releases(osystem)
        self.assertIsInstance(observed, Iterable)
        self.assertEqual(expected, list(observed))


class TestValidateLicenseKeyErrors(MAASTestCase):

    def test_throws_exception_when_os_does_not_exist(self):
        self.assertRaises(
            exceptions.NoSuchOperatingSystem,
            osystems.validate_license_key,
            factory.make_name("no-such-os"),
            factory.make_name("bogus-release"),
            factory.make_name("key-to-not-much"))


class TestValidateLicenseKey(MAASTestCase):

    # Check for every OS and release.
    scenarios = [
        ("%s/%s" % (osystem.name, release),
         {"osystem": osystem, "release": release})
        for _, osystem in OperatingSystemRegistry
        for release in osystem.get_supported_releases()
    ]

    def test_validates_key(self):
        os_specific_validate_license_key = self.patch(
            self.osystem, "validate_license_key")
        osystems.validate_license_key(
            self.osystem.name, self.release, sentinel.key)
        self.assertThat(
            os_specific_validate_license_key,
            MockCalledOnceWith(self.release, sentinel.key))
