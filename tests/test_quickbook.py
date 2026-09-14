from cineworld.quickbook import QuickbookAPI


def test_defaults():
    api = QuickbookAPI()
    try:
        assert api.site_id == "10108"
        assert api.language == "en_GB"
    finally:
        api.close()
