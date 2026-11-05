"""Installation-local state for the optional introduction."""
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

VERSION = 1
LAST_STEP = 4


class IntroductionState(BaseModel):
    status: str
    step: int = Field(ge=0, le=LAST_STEP)


def install(app: FastAPI) -> None:
    def ensure_table():
        with app.state.db.connection() as con:
            con.execute("CREATE TABLE IF NOT EXISTS public_introduction "
                        "(version INTEGER PRIMARY KEY, status TEXT NOT NULL, step INTEGER NOT NULL)")

    @app.get('/introduction', include_in_schema=False)
    def introduction():
        return RedirectResponse('/overview?introduction=start', status_code=303)

    @app.get('/api/introduction', include_in_schema=False)
    def get_introduction():
        ensure_table()
        with app.state.db.connection() as con:
            row = con.execute('SELECT status, step FROM public_introduction WHERE version=?', (VERSION,)).fetchone()
        return {'version': VERSION, 'status': row['status'] if row else 'new', 'step': row['step'] if row else 0}

    @app.post('/api/introduction', include_in_schema=False)
    def set_introduction(state: IntroductionState, request: Request):
        if state.status not in {'active', 'paused', 'dismissed', 'completed'}:
            raise HTTPException(422, 'Ungültiger Einführungsstatus')
        ensure_table()
        with app.state.db.connection() as con:
            con.execute('INSERT INTO public_introduction(version,status,step) VALUES (?,?,?) '
                        'ON CONFLICT(version) DO UPDATE SET status=excluded.status,step=excluded.step',
                        (VERSION, state.status, state.step))
        return {'version': VERSION, 'status': state.status, 'step': state.step}
