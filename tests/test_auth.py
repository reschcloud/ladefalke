def test_ohne_login_weiterleitung(client):
    antwort = client.get("/stammdaten/fahrzeuge", follow_redirects=False)
    assert antwort.status_code == 303
    assert antwort.headers["location"] == "/login?weiter=/stammdaten/fahrzeuge"


def test_ohne_login_htmx_redirect(client):
    antwort = client.get("/", headers={"HX-Request": "true"}, follow_redirects=False)
    assert antwort.headers["HX-Redirect"].startswith("/login")


def test_login_falsch(client):
    antwort = client.post("/login", data={"benutzer": "admin", "passwort": "falsch"})
    assert antwort.status_code == 422
    assert "Benutzername oder Passwort falsch" in antwort.text
    assert client.get("/", follow_redirects=False).status_code == 303


def test_login_richtig_und_weiterleitung(client):
    antwort = client.post("/login", data={"benutzer": "admin", "passwort": "test-passwort",
                                          "weiter_zu": "/stammdaten/msp"}, follow_redirects=False)
    assert antwort.status_code == 303
    assert antwort.headers["location"] == "/stammdaten/msp"
    assert client.get("/").status_code == 200


def test_login_kein_open_redirect(client):
    antwort = client.post("/login", data={"benutzer": "admin", "passwort": "test-passwort",
                                          "weiter_zu": "//boese.example"}, follow_redirects=False)
    assert antwort.headers["location"] == "/"


def test_logout(angemeldet):
    angemeldet.post("/logout")
    assert angemeldet.get("/", follow_redirects=False).status_code == 303


def test_statische_dateien_ohne_login(client):
    assert client.get("/static/pico.min.css").status_code == 200
