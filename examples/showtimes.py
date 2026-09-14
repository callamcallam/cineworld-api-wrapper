from datetime import date, timedelta

from cineworld import Cineworld


with Cineworld() as cw:
    until = (date.today() + timedelta(days=30)).isoformat()
    films = cw.quickbook.films_until(until)
    print(films)
