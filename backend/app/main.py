"""DataChess backend: route assembly only.

Endpoints live in app/routes/ (auth.py, projects.py); shapes in
app/schemas/; dashboard identity in app/routes/deps.py. Anything endpoint
shaped goes in those folders -- this file stays an assembly point.
"""

from fastapi import FastAPI

from .routes import auth, projects

app = FastAPI(title="DataChess")
app.include_router(auth.router)
app.include_router(projects.router)
