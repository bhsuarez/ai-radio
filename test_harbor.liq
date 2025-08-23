# Test minimal Harbor HTTP API
settings.init.allow_root := true

# Simple test endpoint
def test_endpoint(req, response)
  response.json({"status" = "ok", "message" = "Harbor HTTP working"})
end

harbor.http.register(port=8081, method="GET", "/test", test_endpoint)

log("Harbor HTTP test server started on port 8081")
log("Test endpoint: GET /test")

# Keep running
output.dummy(sine())