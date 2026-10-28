from sentinel.graph import build_graph


def test_intake_sets_initial_state_and_checkpoints():
    graph = build_graph()
    config = {"configurable": {"thread_id": "inc-1"}}
    result = graph.invoke(
        {"incident": {"id": "inc-1", "service": "api", "summary": "5xx spike"}},
        config,
    )
    assert result["severity"] == "unknown"
    assert result["evidence"] == []
    assert graph.get_state(config).values == result
