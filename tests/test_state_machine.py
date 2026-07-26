from app.state_machine import JobStatus, can_transition


def test_transitions():
    assert can_transition(JobStatus.CREATED, JobStatus.GOLD_OPTIMIZING)
    assert can_transition(JobStatus.GOLD_OPTIMIZING, JobStatus.GOLD_READY)
    assert can_transition(JobStatus.AWAIT_DECISION, JobStatus.COMPLETED)
    assert not can_transition(JobStatus.COMPLETED, JobStatus.CREATED)


def test_seq_not_in_annotator_system():
    """Ensure annotator system prompt builder does not include seq field name as input key."""
    from app.agents.annotator import AnnotatorAgent

    from app.services.labeling import default_label_schema

    agent = AnnotatorAgent(
        default_label_schema(0.5),
        "rules",
        "prompt",
    )
    sys_p = agent._system()
    # seq may appear in prose rarely; ensure we don't template a seq variable
    assert "{seq}" not in sys_p
    assert "permanent seq" not in sys_p.lower()
    assert "multi-class" in sys_p.lower() or "confidence" in sys_p.lower()
    assert "Label schema:" not in sys_p
