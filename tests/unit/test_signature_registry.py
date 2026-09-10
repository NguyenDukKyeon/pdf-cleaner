from backend.engine.signatures.registry import SignatureRegistry


def test_tailieuonthi_aliases_are_data_driven_and_case_insensitive():
    registry = SignatureRegistry.load_default()
    matches = registry.match_text("footer: https://TaiLieuOnThi.Net")
    assert matches
    assert matches[0].id == "tailieuonthi"


def test_alias_match_is_evidence_not_a_final_confidence_decision():
    registry = SignatureRegistry.load_default()
    match = registry.match_text("TAILIEUONTHI.NET")[0]
    assert match.id == "tailieuonthi"
    assert not hasattr(match, "confidence")
