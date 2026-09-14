from cineworld import Cineworld


with Cineworld(browser_mode="native") as cw:
    print("A normal installed Chrome/Edge window will open.")
    print("Enter your Cineworld login yourself and complete Cloudflare normally.")
    session = cw.login(timeout=300)

    print("Logged in:", session.authenticated)
    print("Session token present:", bool(session.session_token))
