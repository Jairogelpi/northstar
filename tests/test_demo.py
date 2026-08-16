from northstar import demo


def test_demo_runs_the_complete_scoped_approval_loop():
    report = demo.run()

    assert report["disposable"] is True
    assert report["final_decision"] == "ALLOW"
    assert [event["decision"] for event in report["events"]] == [
        "ALLOW",
        "DENY",
        "DENY",
        "ALLOW",
    ]
    assert report["events"][2]["grant"].startswith("public_api:")


def test_demo_render_explains_that_user_files_are_untouched():
    text = demo.render(demo.run())
    assert "temporary checkout" in text
    assert "grant needed" in text
