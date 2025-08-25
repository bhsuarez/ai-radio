# Unit tests for radio.liq functions
# This is a Liquidsoap script that tests individual functions

# Include the helper functions from radio.liq
def meta_get(m, k, d)
  if list.mem(k, list.map(fst, m)) then list.assoc(k, m) else d end
end

def put_default(m, k, v)
  has = list.mem(k, list.map(fst, m))
  cur = if has then list.assoc(k, m) else "" end
  if (not has) or cur == "" then
    m2 = list.filter(fun (kv) -> fst(kv) != k, m)
    list.append(m2, [(k, v)])
  else m end
end

def update_metadata(m)
  m = put_default(m, "title",  "Unknown")
  m = put_default(m, "artist", "Unknown Artist")
  m
end

# Test functions
print("Starting unit tests for radio.liq functions...")

# Test 1: meta_get function
print("\n=== Testing meta_get function ===")
test_metadata = [("artist", "Test Artist"), ("title", "Test Song")]

# Test getting existing key
result1 = meta_get(test_metadata, "artist", "default")
expected1 = "Test Artist"
if result1 == expected1 then
  print("PASS: meta_get existing key")
else
  print("FAIL: meta_get existing key - got '" ^ result1 ^ "', expected '" ^ expected1 ^ "'")
end

# Test getting non-existing key (should return default)
result2 = meta_get(test_metadata, "album", "default_album")
expected2 = "default_album"
if result2 == expected2 then
  print("PASS: meta_get non-existing key")
else
  print("FAIL: meta_get non-existing key - got '" ^ result2 ^ "', expected '" ^ expected2 ^ "'")
end

# Test 2: put_default function
print("\n=== Testing put_default function ===")
empty_metadata = []

# Test adding to empty metadata
result3 = put_default(empty_metadata, "title", "Default Title")
if list.mem("title", list.map(fst, result3)) then
  title_val = list.assoc("title", result3)
  if title_val == "Default Title" then
    print("PASS: put_default on empty metadata")
  else
    print("FAIL: put_default wrong value")
  end
else
  print("FAIL: put_default didn't add key")
end

# Test not overriding existing value
existing_metadata = [("title", "Existing Title")]
result4 = put_default(existing_metadata, "title", "Default Title")
title_val4 = list.assoc("title", result4)
if title_val4 == "Existing Title" then
  print("PASS: put_default preserves existing value")
else
  print("FAIL: put_default overwrote existing value")
end

# Test 3: update_metadata function
print("\n=== Testing update_metadata function ===")
incomplete_metadata = [("artist", "Some Artist")]

result5 = update_metadata(incomplete_metadata)
if list.mem("title", list.map(fst, result5)) and list.mem("artist", list.map(fst, result5)) then
  title_val5 = list.assoc("title", result5)
  artist_val5 = list.assoc("artist", result5)
  if title_val5 == "Unknown" and artist_val5 == "Some Artist" then
    print("PASS: update_metadata adds missing title")
  else
    print("FAIL: update_metadata incorrect values")
  end
else
  print("FAIL: update_metadata missing required keys")
end

print("\n=== Unit Tests Complete ===")

# Create a simple source for testing
test_source = sine(440.0)
shutdown()