from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from database import init_db
from routers import papers, graph, config, prompt, note_images, wiki

app = FastAPI(title="Knowra API", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(papers.router)
app.include_router(graph.router)
app.include_router(config.router)
app.include_router(prompt.router)
app.include_router(note_images.router)
app.include_router(wiki.router)


@app.on_event("startup")
def startup():
    init_db()


@app.get("/")
def root():
    return {"message": "Knowra API is running"}
