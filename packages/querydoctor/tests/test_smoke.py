def test_package_importable():
    import querydoctor

    assert querydoctor.__version__ == "0.1.0"
