"""Post-run official-price supplement; no network calls, writes new artifacts only."""
from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from validation_support import bootstrap as paths
paths.bootstrap()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from validation_drivers import evaluate_virtual_closed_loop_v2 as v
from validation_drivers import evaluate_llm_live as llm
from validation_drivers import audit_virtual_closed_loop_v2 as audit

OUT = audit.OUT


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-secret-read", action="store_true", help="explicit opt-in for in-memory exact secret scan")
    options = parser.parse_args()
    if not options.allow_secret_read or llm.DB_DEFAULT is None or v.KEY_FILE is None:
        parser.error("explicit --allow-secret-read, --cc-switch-db and --jev-key-file required")
    report = json.loads((OUT / "report.json").read_text())
    calls = [json.loads(p.read_text()) for p in sorted(OUT.glob("call-*.json"))]
    rows = {}
    for arm in ("llm_only", "hybrid"):
        owned = [r for r in calls if r["arm"] == arm]
        llm_known = Decimal(0)
        jev_known = Decimal(0)
        output_free_overestimate = Decimal(0)
        missing_l = missing_j = 0
        for call in owned:
            usage = call.get("usage")
            if call["service"] == "llm":
                if not usage:
                    missing_l += 1
                    continue
                hit = usage.get("prompt_cache_hit_tokens", 0)
                miss = usage["prompt_tokens"] - hit
                llm_known += (Decimal(miss) * Decimal(".15") + Decimal(hit) * Decimal(".003") +
                              Decimal(usage["completion_tokens"]) * Decimal(".6")) / 1000000
            else:
                if not usage:
                    missing_j += 1
                    continue
                jev_known += Decimal(usage["input_tokens"]) * Decimal(".042") / 1000000
                output_free_overestimate += Decimal(usage["output_tokens"]) * Decimal(".042") / 1000000
        rows[arm] = {"official_offpeak_llm_known_usage_subtotal_usd": float(llm_known),
                     "official_offpeak_llm_total_estimate_usd": None if missing_l else float(llm_known),
                     "llm_unknown_usage_attempts": missing_l,
                     "official_input_only_jev_estimate_usd": None if missing_j else float(jev_known),
                     "official_input_only_jev_known_subtotal_usd": float(jev_known),
                     "jev_unknown_usage_calls": missing_j,
                     "driver_conservative_jev_output_charge_overestimate_usd": float(output_free_overestimate),
                     "combined_known_subtotal_usd": float(llm_known + jev_known),
                     "combined_total_estimate_usd": None if missing_l or missing_j else float(llm_known + jev_known),
                     "actual_invoice": None}
    supplement = {"scope": "supplement_only_original_report_per_call_configured_estimates_not_overwritten",
                  "source_urls": ["https://api-docs.deepseek.com/quick_start/pricing", "https://docs.typesafe.ai/models"],
                  "retrieved_via": "Tavily 2026-10-01",
                  "deepseek_offpeak_rates_per_million": {"input_cache_miss": .15, "input_cache_hit": .003, "output": .6},
                  "jev_rates_per_million": {"input": .042, "output": 0},
                  "timestamp_basis": "All observed starts are 2026-10-01 UTC after 10:00; offpeak per official table. Chinese public holidays also offpeak.",
                  "not_verified_invoice_or_account_billing": True,
                  "configuration_price_lower_than_current_official_tariff": True,
                  "complete_300_llm_offpeak_reserve_usd": float((Decimal(16096) * Decimal(".15") + Decimal(512) * Decimal(".6")) * 300 / 1000000),
                  "attempted_llm_offpeak_reserve_usd": float((Decimal(16096) * Decimal(".15") + Decimal(512) * Decimal(".6")) * report["llm_budget"]["calls_reserved"] / 1000000),
                  "peak_rate_would_exceed_1usd_full_reserve_do_not_reuse_run_in_peak_without_new_design": True,
                  "summaries": rows, "actual_invoice": None}
    # Assert every timestamp is offpeak by UTC time; no current tariff extrapolation to peak.
    for call in calls:
        if call["service"] == "llm":
            hour = v.datetime.fromisoformat(call["start_utc"]).hour
            if 1 <= hour < 4 or 6 <= hour < 10:
                raise ValueError("peak_timestamp_not_supported_by_supplement")
    llm.write_json(OUT / "official-price-supplement.json", supplement)
    result = audit.audit()
    llm.write_json(OUT / "independent-audit.json", result)
    provider = llm.read_provider(llm.DB_DEFAULT, v.PROVIDER_ID, "openai")
    key = v.read_first_key()
    scans = {p.name: llm.scan_secrets(p, [provider.secret, key]) for p in
             (v.EVIDENCE, OUT, paths.artifacts_path("2026-10-01-llm-v2-checks"))}
    if any(r["exact_in_memory_secret_matches"] for r in scans.values()):
        raise ValueError("exact_secret_match")
    llm.write_json(OUT / "final-exact-secret-scan.json", scans)
    print(json.dumps({"audit": result, "official_price_summaries": rows,
                      "offpeak_complete_reserve": supplement["complete_300_llm_offpeak_reserve_usd"],
                      "secret_matches": 0}, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        print(json.dumps({"status": "failed", "code": "finalization_error_redacted"}), file=sys.stderr)
        raise SystemExit(2)
