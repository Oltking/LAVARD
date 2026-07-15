"""The integrated demo runs end-to-end and produces the money + memory story."""

from demo import run_demo


def test_run_demo_produces_all_sections():
    r = run_demo()
    # 1. Intelligence Exchange collapsed the burst
    assert r["intelligence_exchange"]["upstream_calls"] == 1
    assert r["intelligence_exchange"]["shared_hits"] == 49
    # 2. preference produces a ranked pick (may or may not differ on random marketplace data)
    assert r["preference"]["cheapest_pick"] and r["preference"]["smartest_pick"]
    # 3. memory: second job reused memory (skipped hires) OR reused crew
    mem = r["memory"]
    assert mem["nodes_skipped_from_memory_job2"] + mem["crew_reused_job2"] >= 1
    assert mem["spend_job2"] <= mem["spend_job1"]
    # 4. OS overview present with three network effects
    assert set(r["os"]["network_effects"]) == {"memory", "liquidity", "reputation"}
