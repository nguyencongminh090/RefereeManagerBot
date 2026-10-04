import threading
import unittest

from serverapp.backup   import BackupSchedule
from serverapp.claims   import ClaimRegistry
from serverapp.router   import PacketRouter
from serverapp.sessions import AuthLockout, SessionRegistry

A, B = ("10.0.0.1", 1111), ("10.0.0.2", 2222)


class FakeClock:
    def __init__(self):
        self.now = 100.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class ClaimRegistryTests(unittest.TestCase):
    def test_first_claim_wins_and_owner_can_reclaim(self):
        claims = ClaimRegistry()
        self.assertEqual(A, claims.claim(5, A))
        self.assertEqual(A, claims.claim(5, A))
        self.assertEqual(A, claims.claim(5, B))
        self.assertEqual(A, claims.owner(5))

    def test_release_only_by_owner(self):
        claims = ClaimRegistry()
        claims.claim(5, A)
        self.assertFalse(claims.release(5, B))
        self.assertTrue(claims.release(5, A))
        self.assertIsNone(claims.owner(5))
        self.assertFalse(claims.release(5, A))

    def test_release_all_for_one_address(self):
        claims = ClaimRegistry()
        claims.claim(1, A)
        claims.claim(2, A)
        claims.claim(3, B)
        claims.release_all_for(A)
        self.assertEqual((None, None, B), (claims.owner(1), claims.owner(2), claims.owner(3)))

    def test_concurrent_claims_have_one_winner(self):
        claims, winners = ClaimRegistry(), []
        def grab(addr):
            winners.append(claims.claim(9, addr))
        threads = [threading.Thread(target=grab, args=(("h", i),)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(1, len(set(winners)))


class SessionRegistryTests(unittest.TestCase):
    def setUp(self):
        self.clock    = FakeClock()
        self.sessions = SessionRegistry(self.clock, heartbeat_timeout_seconds=30, auth_timeout_seconds=5)

    def test_authenticated_bots_are_listed_with_names(self):
        self.sessions.connect(A)
        self.sessions.connect(B)
        self.sessions.authenticate(A, "bot1")
        self.assertEqual("bot1", self.sessions.name_of(A))
        self.assertIsNone(self.sessions.name_of(B))
        self.assertEqual([A], self.sessions.authenticated())
        self.assertTrue(self.sessions.is_authenticated(A))
        self.sessions.disconnect(A)
        self.assertEqual([], self.sessions.authenticated())

    def test_connection_without_auth_expires_after_deadline(self):
        self.sessions.connect(A)
        self.clock.advance(4.9)
        self.sessions.touch(A)                      # traffic does not extend the auth deadline
        self.assertEqual([], self.sessions.pop_expired())
        self.clock.advance(0.2)
        self.sessions.touch(A)
        self.assertEqual([(A, "no authentication")], self.sessions.pop_expired())
        self.assertEqual([], self.sessions.pop_expired())

    def test_authenticated_bot_is_not_subject_to_auth_deadline(self):
        self.sessions.connect(A)
        self.sessions.authenticate(A, "bot1")
        self.clock.advance(10)
        self.assertEqual([], self.sessions.pop_expired())

    def test_silent_bot_expires_after_heartbeat_timeout(self):
        self.sessions.connect(A)
        self.sessions.authenticate(A, "bot1")
        self.clock.advance(29)
        self.sessions.touch(A)
        self.clock.advance(29)
        self.assertEqual([], self.sessions.pop_expired())
        self.clock.advance(2)
        self.assertEqual([(A, "no heartbeat")], self.sessions.pop_expired())


class AuthLockoutTests(unittest.TestCase):
    def setUp(self):
        self.clock   = FakeClock()
        self.lockout = AuthLockout(max_failures=3, window_seconds=60, clock=self.clock)

    def test_locks_after_more_than_max_failures(self):
        for _ in range(3):
            self.assertFalse(self.lockout.record_failure("1.2.3.4"))
        self.assertFalse(self.lockout.is_locked("1.2.3.4"))
        self.assertTrue(self.lockout.record_failure("1.2.3.4"))
        self.assertTrue(self.lockout.is_locked("1.2.3.4"))
        self.assertFalse(self.lockout.is_locked("5.6.7.8"))

    def test_lockout_ends_after_the_window(self):
        for _ in range(4):
            self.lockout.record_failure("1.2.3.4")
        self.clock.advance(59)
        self.assertTrue(self.lockout.is_locked("1.2.3.4"))
        self.clock.advance(2)
        self.assertFalse(self.lockout.is_locked("1.2.3.4"))
        self.assertFalse(self.lockout.record_failure("1.2.3.4"))      # the count starts again

    def test_old_failures_fall_out_of_the_window(self):
        for _ in range(3):
            self.lockout.record_failure("1.2.3.4")
        self.clock.advance(61)
        self.assertFalse(self.lockout.record_failure("1.2.3.4"))
        self.assertFalse(self.lockout.is_locked("1.2.3.4"))

    def test_success_clears_the_failures(self):
        for _ in range(3):
            self.lockout.record_failure("1.2.3.4")
        self.lockout.clear("1.2.3.4")
        self.assertFalse(self.lockout.record_failure("1.2.3.4"))


class BackupScheduleTests(unittest.TestCase):
    def test_runs_only_when_interval_has_passed(self):
        clock, runs = FakeClock(), []
        schedule = BackupSchedule(lambda: runs.append(clock.now), 300, clock)
        self.assertFalse(schedule.tick())
        clock.advance(299)
        self.assertFalse(schedule.tick())
        clock.advance(2)
        self.assertTrue(schedule.tick())
        self.assertFalse(schedule.tick())
        clock.advance(300)
        self.assertTrue(schedule.tick())
        self.assertEqual(2, len(runs))

    def test_failing_action_is_logged_and_rescheduled(self):
        clock = FakeClock()
        def boom():
            raise OSError("disk full")
        schedule = BackupSchedule(boom, 10, clock)
        clock.advance(11)
        with self.assertLogs("serverapp.backup", "ERROR"):
            self.assertTrue(schedule.tick())
        self.assertFalse(schedule.tick())


class PacketRouterTests(unittest.TestCase):
    def test_routes_by_type_and_reports_unknown(self):
        seen = []
        router = PacketRouter({4: lambda addr, packet: seen.append((addr, packet))})
        self.assertTrue(router.route(A, {"type": 4}))
        self.assertEqual([(A, {"type": 4})], seen)
        self.assertFalse(router.route(A, {"type": 99}))
        self.assertFalse(router.route(A, ["not", "a", "dict"]))


if __name__ == "__main__":
    unittest.main()
