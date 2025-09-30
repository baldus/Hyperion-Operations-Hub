from invapp import create_app

app = create_app()

if __name__ == "__main__":
    # Same as before — runs on all interfaces so you can hit it from other devices
    app.run(host="0.0.0.0", port=5000, debug=True)
