from unittest.mock import patch

from tools.faab_history.check_candidate_history import check_faab_enabled, check_year, _usable


class _FakeResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


def _settings_payload(uses_faab: bool) -> dict:
    return {"settings": {"acquisitionSettings": {"isUsingAcquisitionBudget": uses_faab}}}


def test_check_faab_enabled_true_for_a_real_faab_season():
    # The O League's own 2019 season - the year FAAB auctions actually started
    # for it (see pull_o_league_bids.py's --start-year default).
    with patch("tools.faab_history.league_profile.requests.get") as mock_get:
        mock_get.return_value = _FakeResponse(200, _settings_payload(True))
        assert check_faab_enabled(355398, 2019) is True


def test_check_faab_enabled_false_for_a_pre_faab_season():
    # A season this league ran under plain waiver priority, not FAAB - e.g.
    # what The O League's own history would show before it switched onto FAAB
    # in 2019, IF that season's settings were even publicly readable (they're
    # not - see the credentials-required test below - but a different league
    # that switched onto FAAB partway through its own history could have a
    # real accessible-but-pre-FAAB season shaped exactly like this).
    with patch("tools.faab_history.league_profile.requests.get") as mock_get:
        mock_get.return_value = _FakeResponse(200, _settings_payload(False))
        assert check_faab_enabled(355398, 2016) is False


def test_check_faab_enabled_none_when_settings_need_credentials():
    # The O League's own real behavior for 2018/2019 unauthenticated (confirmed
    # live 2026-09-16): mSettings 401s just like mTransactions2 does. None, not
    # False, so a caller can't mistake "couldn't check" for "confirmed non-FAAB".
    with patch("tools.faab_history.league_profile.requests.get") as mock_get:
        mock_get.return_value = _FakeResponse(401)
        assert check_faab_enabled(355398, 2018) is None


def test_check_year_and_faab_enabled_together_exclude_a_pre_faab_but_accessible_season():
    # The scenario this whole check exists for: a season whose transactions
    # ARE readable (so the old accessible-only logic would have kept it) but
    # that season was never actually running FAAB.
    with patch("tools.faab_history.check_candidate_history.requests.get") as mock_txn:
        mock_txn.return_value = _FakeResponse(200, {"transactions": []})
        txn_status = check_year(12345, 2017)
    with patch("tools.faab_history.league_profile.requests.get") as mock_settings:
        mock_settings.return_value = _FakeResponse(200, _settings_payload(False))
        faab = check_faab_enabled(12345, 2017)

    assert txn_status == "accessible"
    assert faab is False
    assert _usable(txn_status, faab) is False


def test_usable_requires_both_accessible_transactions_and_confirmed_faab():
    assert _usable("accessible", True) is True
    assert _usable("accessible", False) is False
    assert _usable("accessible", None) is False
    assert _usable("private(401)", True) is False
