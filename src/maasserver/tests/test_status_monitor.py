# Copyright 2016-2017 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Tests for the status monitor module."""

__all__ = []


from datetime import (
    datetime,
    timedelta,
)
from unittest.mock import call

from maasserver import status_monitor
from maasserver.enum import NODE_STATUS
from maasserver.models import Node
from maasserver.models.signals.testing import SignalsDisabled
from maasserver.node_status import NODE_FAILURE_MONITORED_STATUS_TRANSITIONS
from maasserver.status_monitor import (
    mark_nodes_failed_after_expiring,
    mark_nodes_failed_after_missing_script_timeout,
    StatusMonitorService,
)
from maasserver.testing.factory import factory
from maasserver.testing.testcase import MAASServerTestCase
from maasserver.utils.orm import reload_object
from maastesting.djangotestcase import CountQueries
from maastesting.matchers import (
    MockCalledOnce,
    MockCalledOnceWith,
    MockCallsMatch,
    MockNotCalled,
)
from metadataserver.enum import SCRIPT_STATUS
from metadataserver.models import ScriptSet
from provisioningserver.refresh.node_info_scripts import NODE_INFO_SCRIPTS
from twisted.internet.defer import maybeDeferred
from twisted.internet.task import Clock


class TestMarkNodesFailedAfterExpiring(MAASServerTestCase):

    def test__marks_all_possible_failed_status_as_failed(self):
        self.useFixture(SignalsDisabled("power"))
        current_time = datetime.now()
        self.patch(status_monitor, "now").return_value = current_time
        expired_time = current_time - timedelta(minutes=1)
        nodes = [
            factory.make_Node(status=status, status_expires=expired_time)
            for status in NODE_FAILURE_MONITORED_STATUS_TRANSITIONS.keys()
        ]
        mark_nodes_failed_after_expiring()
        failed_statuses = [
            reload_object(node).status
            for node in nodes
        ]
        self.assertItemsEqual(
            NODE_FAILURE_MONITORED_STATUS_TRANSITIONS.values(),
            failed_statuses)

    def test__skips_those_that_have_not_expired(self):
        self.useFixture(SignalsDisabled("power"))
        current_time = datetime.now()
        self.patch(status_monitor, "now").return_value = current_time
        expired_time = current_time + timedelta(minutes=1)
        nodes = [
            factory.make_Node(status=status, status_expires=expired_time)
            for status in NODE_FAILURE_MONITORED_STATUS_TRANSITIONS.keys()
        ]
        mark_nodes_failed_after_expiring()
        failed_statuses = [
            reload_object(node).status
            for node in nodes
        ]
        self.assertItemsEqual(
            NODE_FAILURE_MONITORED_STATUS_TRANSITIONS.keys(), failed_statuses)


class TestMarkNodesFailedAfterMissingScriptTimeout(MAASServerTestCase):

    scenarios = (
        ('commissioning', {
            'status': NODE_STATUS.COMMISSIONING,
            'failed_status': NODE_STATUS.FAILED_COMMISSIONING,
        }),
        ('testing', {
            'status': NODE_STATUS.TESTING,
            'failed_status': NODE_STATUS.FAILED_TESTING,
        }),
    )

    def setUp(self):
        super().setUp()
        self.useFixture(SignalsDisabled("power"))
        self.mock_stop = self.patch(Node, 'stop')

    def make_node(self):
        user = factory.make_admin()
        node = factory.make_Node(
            status=self.status, with_empty_script_sets=True, owner=user,
            enable_ssh=factory.pick_bool())
        if self.status == NODE_STATUS.COMMISSIONING:
            script_set = node.current_commissioning_script_set
        elif self.status == NODE_STATUS.TESTING:
            script_set = node.current_testing_script_set
        return node, script_set

    def test_mark_nodes_handled_last_ping_None(self):
        node, script_set = self.make_node()
        script_set.last_ping = None
        script_set.save()
        for _ in range(3):
            factory.make_ScriptResult(
                script_set=script_set, status=SCRIPT_STATUS.PENDING)

        # No exception should be raised.
        mark_nodes_failed_after_missing_script_timeout()
        node = reload_object(node)
        self.assertEquals(self.status, node.status)

    def test_mark_nodes_failed_after_missing_timeout_heartbeat(self):
        node, script_set = self.make_node()
        script_set.last_ping = datetime.now() - timedelta(minutes=11)
        script_set.save()
        script_results = [
            factory.make_ScriptResult(
                script_set=script_set, status=SCRIPT_STATUS.PENDING)
            for _ in range(3)
        ]

        mark_nodes_failed_after_missing_script_timeout()
        node = reload_object(node)

        self.assertEquals(self.failed_status, node.status)
        self.assertEquals(
            'Node has missed the last 5 heartbeats', node.error_description)
        if node.enable_ssh:
            self.assertThat(self.mock_stop, MockNotCalled())
        else:
            self.assertThat(self.mock_stop, MockCalledOnce())
        for script_result in script_results:
            self.assertEquals(
                SCRIPT_STATUS.TIMEDOUT, reload_object(script_result).status)

    def test_mark_nodes_failed_after_script_overrun(self):
        node, script_set = self.make_node()
        now = datetime.now()
        script_set.last_ping = now
        script_set.save()
        passed_script_result = factory.make_ScriptResult(
            script_set=script_set, status=SCRIPT_STATUS.PASSED)
        failed_script_result = factory.make_ScriptResult(
            script_set=script_set, status=SCRIPT_STATUS.FAILED)
        pending_script_result = factory.make_ScriptResult(
            script_set=script_set, status=SCRIPT_STATUS.PENDING)
        script = factory.make_Script(timeout=timedelta(seconds=60))
        running_script_result = factory.make_ScriptResult(
            script_set=script_set, status=SCRIPT_STATUS.RUNNING, script=script,
            started=now - timedelta(minutes=10))

        mark_nodes_failed_after_missing_script_timeout()
        node = reload_object(node)

        self.assertEquals(self.failed_status, node.status)
        self.assertEquals(
            "%s has run past it's timeout(%s)" % (
                running_script_result.name,
                str(running_script_result.script.timeout)),
            node.error_description)
        if node.enable_ssh:
            self.assertThat(self.mock_stop, MockNotCalled())
        else:
            self.assertThat(self.mock_stop, MockCalledOnce())
        self.assertEquals(
            SCRIPT_STATUS.PASSED, reload_object(passed_script_result).status)
        self.assertEquals(
            SCRIPT_STATUS.FAILED, reload_object(failed_script_result).status)
        self.assertEquals(
            SCRIPT_STATUS.ABORTED,
            reload_object(pending_script_result).status)
        self.assertEquals(
            SCRIPT_STATUS.TIMEDOUT,
            reload_object(running_script_result).status)

    def test_mark_nodes_failed_after_builtin_commiss_script_overrun(self):
        user = factory.make_admin()
        node = factory.make_Node(status=NODE_STATUS.COMMISSIONING, owner=user)
        script_set = ScriptSet.objects.create_commissioning_script_set(node)
        node.current_commissioning_script_set = script_set
        node.save()
        now = datetime.now()
        script_set.last_ping = now
        script_set.save()
        pending_script_results = list(script_set.scriptresult_set.all())
        passed_script_result = pending_script_results.pop()
        passed_script_result.status = SCRIPT_STATUS.PASSED
        passed_script_result.save()
        failed_script_result = pending_script_results.pop()
        failed_script_result.status = SCRIPT_STATUS.FAILED
        failed_script_result.save()
        running_script_result = pending_script_results.pop()
        running_script_result.status = SCRIPT_STATUS.RUNNING
        running_script_result.started = now - timedelta(minutes=10)
        running_script_result.save()

        mark_nodes_failed_after_missing_script_timeout()
        node = reload_object(node)

        self.assertEquals(NODE_STATUS.FAILED_COMMISSIONING, node.status)
        self.assertEquals(
            "%s has run past it's timeout(%s)" % (
                running_script_result.name,
                str(NODE_INFO_SCRIPTS[running_script_result.name]['timeout'])),
            node.error_description)
        if node.enable_ssh:
            self.assertThat(self.mock_stop, MockNotCalled())
        else:
            self.assertThat(self.mock_stop, MockCalledOnce())
        self.assertEquals(
            SCRIPT_STATUS.PASSED, reload_object(passed_script_result).status)
        self.assertEquals(
            SCRIPT_STATUS.FAILED, reload_object(failed_script_result).status)
        self.assertEquals(
            SCRIPT_STATUS.TIMEDOUT,
            reload_object(running_script_result).status)
        for script_result in pending_script_results:
            self.assertEquals(
                SCRIPT_STATUS.ABORTED, reload_object(script_result).status)

    def test_uses_param_runtime(self):
        node, script_set = self.make_node()
        now = datetime.now()
        script_set.last_ping = now
        script_set.save()
        passed_script_result = factory.make_ScriptResult(
            script_set=script_set, status=SCRIPT_STATUS.PASSED)
        failed_script_result = factory.make_ScriptResult(
            script_set=script_set, status=SCRIPT_STATUS.FAILED)
        pending_script_result = factory.make_ScriptResult(
            script_set=script_set, status=SCRIPT_STATUS.PENDING)
        script = factory.make_Script(timeout=timedelta(minutes=2))
        running_script_result = factory.make_ScriptResult(
            script_set=script_set, status=SCRIPT_STATUS.RUNNING, script=script,
            started=now - timedelta(minutes=50), parameters={'runtime': {
                'type': 'runtime',
                'value': 60 * 60,
                }})

        mark_nodes_failed_after_missing_script_timeout()
        node = reload_object(node)

        self.assertEquals(self.status, node.status)
        self.assertThat(self.mock_stop, MockNotCalled())
        self.assertEquals(
            SCRIPT_STATUS.PASSED, reload_object(passed_script_result).status)
        self.assertEquals(
            SCRIPT_STATUS.FAILED, reload_object(failed_script_result).status)
        self.assertEquals(
            SCRIPT_STATUS.PENDING,
            reload_object(pending_script_result).status)
        self.assertEquals(
            SCRIPT_STATUS.RUNNING,
            reload_object(running_script_result).status)

    def test_mark_nodes_failed_after_missing_timeout_prefetches(self):
        self.patch(Node, 'mark_failed')
        now = datetime.now()
        nodes = []
        for _ in range(3):
            node, script_set = self.make_node()
            script_set.last_ping = now
            script_set.save()
            script = factory.make_Script(timeout=timedelta(seconds=60))
            factory.make_ScriptResult(
                script_set=script_set, status=SCRIPT_STATUS.RUNNING,
                script=script, started=now - timedelta(minutes=3))
            nodes.append(node)

        counter = CountQueries()
        with counter:
            mark_nodes_failed_after_missing_script_timeout()
        # Initial lookup and prefetch take three queries. This is done once to
        # find the nodes which nodes are being tests and on each node which
        # scripts are currently running.
        self.assertEquals(3 + len(nodes) * 2, counter.num_queries)


class TestStatusMonitorService(MAASServerTestCase):

    def test_init_with_default_interval(self):
        # The service itself calls `check_status` in a thread, via a couple of
        # decorators. This indirection makes it clearer to mock
        # `cleanup_old_nonces` here and track calls to it.
        mock_check_status = self.patch(status_monitor, "check_status")
        # Making `deferToDatabase` use the current thread helps testing.
        self.patch(status_monitor, "deferToDatabase", maybeDeferred)

        service = StatusMonitorService()
        # Use a deterministic clock instead of the reactor for testing.
        service.clock = Clock()

        # The interval is stored as `step` by TimerService,
        # StatusMonitorService's parent class.
        interval = 60  # seconds.
        self.assertEqual(service.step, interval)

        # `check_status` is not called before the service is started.
        self.assertThat(mock_check_status, MockNotCalled())
        # `check_status` is called the moment the service is started.
        service.startService()
        self.assertThat(mock_check_status, MockCalledOnceWith())
        # Advancing the clock by `interval - 1` means that
        # `mark_nodes_failed_after_expiring` has still only been called once.
        service.clock.advance(interval - 1)
        self.assertThat(mock_check_status, MockCalledOnceWith())
        # Advancing the clock one more second causes another call to
        # `check_status`.
        service.clock.advance(1)
        self.assertThat(mock_check_status, MockCallsMatch(call(), call()))

    def test_interval_can_be_set(self):
        interval = self.getUniqueInteger()
        service = StatusMonitorService(interval)
        self.assertEqual(interval, service.step)
