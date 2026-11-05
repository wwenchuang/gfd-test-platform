from task_server.services.ui_video_analysis import _overlapping_return_review


def test_shared_return_frames_require_review_without_dropping_either_action():
    events=[{'action':'navigateBack','frame_refs':['frame-338','frame-339','frame-340']},
            {'action':'navigateBack','frame_refs':['frame-339','frame-340']},
            {'action':'navigateBack','frame_refs':['frame-341','frame-342']},
            {'action':'navigateBack','frame_refs':['frame-341','frame-342','frame-343']}]
    before=[dict(e) for e in events]
    questions=_overlapping_return_review(events)
    assert len(questions)==2 and all('核对' in q for q in questions)
    assert events==before


def test_adjacent_returns_sharing_only_context_frame_are_not_flagged():
    events=[{'action':'navigateBack','frame_refs':['frame-331','frame-332']},
            {'action':'navigateBack','frame_refs':['frame-332','frame-333']}]
    assert not _overlapping_return_review(events)
