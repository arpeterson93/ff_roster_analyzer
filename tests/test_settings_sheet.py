import pytest

from ingest.settings_sheet import SettingsSheetError, apply_remote_settings, parse_seeding_config, parse_settings_csv

SAMPLE_CSV = """league_slug,key,value
o-league,matchup_dampening,0.6
o-league,pa_basis,l5
any-given-sunday,matchup_dampening,0.4
any-given-sunday,division_winners_first,false
"""


def test_parse_settings_csv_groups_by_league():
    result = parse_settings_csv(SAMPLE_CSV)
    assert result["o-league"] == {"matchup_dampening": "0.6", "pa_basis": "l5"}
    assert result["any-given-sunday"] == {"matchup_dampening": "0.4", "division_winners_first": "false"}


def test_parse_settings_csv_skips_blank_rows():
    csv_text = "league_slug,key,value\n,,\no-league,matchup_dampening,0.5\n"
    result = parse_settings_csv(csv_text)
    assert result == {"o-league": {"matchup_dampening": "0.5"}}


def test_parse_settings_csv_missing_columns_raises():
    with pytest.raises(SettingsSheetError):
        parse_settings_csv("foo,bar\n1,2\n")


def _base_cfg():
    return {
        "valuation": {"matchup_dampening": 0.5, "pa_basis": "blend", "pa_l5_weight": 0.5},
        "sim": {"division_winners_first": True},
    }


def test_apply_remote_settings_overrides_known_keys():
    cfg = _base_cfg()
    changes = apply_remote_settings(cfg, {"matchup_dampening": "0.75", "pa_basis": "season"})
    assert cfg["valuation"]["matchup_dampening"] == pytest.approx(0.75)
    assert cfg["valuation"]["pa_basis"] == "season"
    assert len(changes) == 2


def test_apply_remote_settings_casts_booleans():
    cfg = _base_cfg()
    apply_remote_settings(cfg, {"division_winners_first": "false"})
    assert cfg["sim"]["division_winners_first"] is False


def test_apply_remote_settings_ignores_unknown_keys():
    cfg = _base_cfg()
    changes = apply_remote_settings(cfg, {"totally_made_up_key": "123"})
    assert changes == []
    assert cfg == _base_cfg()


def test_apply_remote_settings_ignores_invalid_values():
    cfg = _base_cfg()
    changes = apply_remote_settings(cfg, {"matchup_dampening": "not-a-number"})
    assert changes == []
    assert cfg["valuation"]["matchup_dampening"] == 0.5  # unchanged


def test_apply_remote_settings_no_change_reports_nothing():
    cfg = _base_cfg()
    changes = apply_remote_settings(cfg, {"matchup_dampening": "0.5"})
    assert changes == []


def test_apply_remote_settings_collects_seeding_keys_verbatim():
    cfg = _base_cfg()
    changes = apply_remote_settings(
        cfg,
        {
            "division_tiebreak_1": "wins",
            "division_tiebreak_2": "points_for",
            "seed_1_division_priority": "true",
            "seed_6_division_priority": "false",
            "seed_6_tiebreak_1": "points_for",
        },
    )
    assert cfg["sim"]["seeding_raw"] == {
        "division_tiebreak_1": "wins",
        "division_tiebreak_2": "points_for",
        "seed_1_division_priority": "true",
        "seed_6_division_priority": "false",
        "seed_6_tiebreak_1": "points_for",
    }
    assert len(changes) == 5


def test_parse_seeding_config_builds_division_order_and_per_seed_configs():
    division_order, seed_configs = parse_seeding_config(
        {
            "division_tiebreak_1": "wins",
            "division_tiebreak_2": "points_for",
            "seed_1_division_priority": "true",
            "seed_6_division_priority": "false",
            "seed_6_tiebreak_1": "points_for",
            "seed_6_tiebreak_2": "wins",
        }
    )
    assert division_order == ["wins", "points_for"]
    assert seed_configs[1].division_priority is True
    assert seed_configs[1].tiebreak_order == []
    assert seed_configs[6].division_priority is False
    assert seed_configs[6].tiebreak_order == ["points_for", "wins"]


def test_parse_seeding_config_empty_input_is_empty_output():
    division_order, seed_configs = parse_seeding_config({})
    assert division_order == []
    assert seed_configs == {}
