from invapp import create_app

app = create_app()

if __name__ == "__main__":
    # Development fallback: the production entry point now uses Gunicorn.
    app.run(host="0.0.0.0", port=5000)
