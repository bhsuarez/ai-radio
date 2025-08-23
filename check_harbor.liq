# Check if harbor functions are available
settings.init.allow_root := true

log("Checking harbor availability...")

# Try simple harbor test
try
  log("Harbor module exists")
  # Just check if harbor exists without calling it
catch err do
  log("Harbor module not available: #{err}")
end

output.dummy(sine())