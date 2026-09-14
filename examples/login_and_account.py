from getpass import getpass

from cineworld import Cineworld


email = input("Cineworld email: ").strip()
password = getpass("Cineworld password: ")

with Cineworld(browser_mode="native") as cw:
    print("Opening your installed Chrome/Edge with a dedicated Cineworld profile...")
    print("Your details will be filled, but YOU should click Log in in the browser.")
    print("Complete any Cloudflare/Turnstile prompt normally if it appears.\n")

    session = cw.login(email, password, auto_submit=False)

    print("\nLogged in successfully")
    print("Session token present:", bool(session.session_token))
    print("Membership ID present:", bool(session.membership_id))

    member = cw.get_member()
    print("Member:", member.get("FirstName") or "(name unavailable)")

    orders = cw.get_orders()
    print("Orders loaded:", type(orders).__name__)
