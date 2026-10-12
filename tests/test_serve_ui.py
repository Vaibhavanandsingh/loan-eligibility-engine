from src.handlers import serve_ui


def test_serves_the_upload_page():
    response = serve_ui.handler({}, None)
    assert response["statusCode"] == 200
    assert response["headers"]["Content-Type"].startswith("text/html")
    assert "<title>Loan Eligibility Engine" in response["body"]
    # Without ?api= the page calls its own origin, i.e. this API.
    assert "get('api') || ''" in response["body"]
