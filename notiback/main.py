from fastapi import FastAPI

app = FastAPI()


@app.get("/")
def read_base_page():
    return {"status": True, "dimas": "goat"}
