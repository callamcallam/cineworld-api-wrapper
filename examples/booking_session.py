from getpass import getpass

from cineworld import Cineworld


email = input("Cineworld email: ").strip()
password = getpass("Cineworld password: ")

with Cineworld() as cw:
    cw.login(email, password)
    member = cw.create_vista_session()
    print("Vista UserSessionId created:", bool(member.get("UserSessionId")))
