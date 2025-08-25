# AI Radio Test Suite

This directory contains comprehensive tests for the AI Radio system, specifically focusing on the `radio.liq` Liquidsoap configuration.

## Test Files

### 1. `test_radio_liq.py`
**Comprehensive Configuration Test**
- Tests Liquidsoap configuration syntax validation
- Verifies required files exist
- Checks port accessibility (telnet, icecast, harbor, flask)
- Tests telnet command interface
- Validates stream endpoints
- Checks Flask API endpoints
- Verifies TTS script availability
- Validates configuration values and function definitions

### 2. `test_radio_functional.sh`
**System Functionality Test**
- Tests running Docker containers
- Validates service connectivity
- Tests stream accessibility
- Checks metadata API responses
- Tests TTS queue functionality
- Validates systemd service status
- Checks log activity and system health

### 3. `test_radio_unit.liq`
**Unit Tests for Liquidsoap Functions**
- Tests `meta_get()` function with various inputs
- Tests `put_default()` metadata handling
- Tests `update_metadata()` function
- Validates core helper function behavior

## Running Tests

### Quick Test
```bash
# Run comprehensive configuration test
python3 test_radio_liq.py

# Run functional system test
./test_radio_functional.sh
```

### Unit Test (requires liquidsoap)
```bash
# Test individual functions (if liquidsoap command available)
liquidsoap test_radio_unit.liq
```

### All Tests
```bash
# Run all tests in sequence
python3 test_radio_liq.py && ./test_radio_functional.sh
```

## Test Coverage

### Configuration Tests (`test_radio_liq.py`)
✅ Liquidsoap syntax validation  
✅ Required file existence  
✅ Port accessibility (1234, 8000, 8001, 5055)  
✅ Telnet command interface  
✅ Stream endpoint accessibility  
✅ Flask API endpoint validation  
✅ TTS script availability  
✅ Configuration value validation  
✅ Function definition checks  

### Functional Tests (`test_radio_functional.sh`)
✅ Docker container status  
✅ Service connectivity  
✅ Stream accessibility  
✅ Metadata API functionality  
✅ TTS queue operations  
✅ Systemd service status  
✅ Log activity monitoring  
✅ System health checks  

### Unit Tests (`test_radio_unit.liq`)
✅ `meta_get()` function behavior  
✅ `put_default()` metadata handling  
✅ `update_metadata()` processing  

## Understanding Results

### Test Status Codes
- **PASS**: Test completed successfully
- **FAIL**: Test failed, indicating potential issue
- **WARN**: Test completed with warnings, may indicate minor issues

### Success Rate Interpretation
- **90-100%**: Excellent, system fully functional
- **80-89%**: Good, minor issues that don't affect core functionality
- **70-79%**: Acceptable, some issues that should be addressed
- **<70%**: Poor, significant issues requiring attention

## Common Test Failures and Solutions

### "liquidsoap command not found"
- **Cause**: Liquidsoap not installed in PATH
- **Impact**: Cannot validate syntax, but running system may still work
- **Solution**: Install liquidsoap package or run tests on system with liquidsoap

### "Port not accessible"
- **Cause**: Service not running or port blocked
- **Impact**: Functionality related to that port will not work
- **Solution**: Check service status with `systemctl status <service>`

### "Stream returned status code: 400"
- **Cause**: Icecast not fully initialized or no source connected
- **Impact**: Stream may not be available to listeners
- **Solution**: Check Liquidsoap container logs and restart if needed

### "Telnet interface not responding"
- **Cause**: Liquidsoap container not running or telnet disabled
- **Impact**: TTS enqueue and control commands will fail
- **Solution**: Restart ai-radio service and check container status

## Integration with Development Workflow

### Before Deployment
```bash
# Run full test suite before deploying changes
python3 test_radio_liq.py && ./test_radio_functional.sh
```

### After Configuration Changes
```bash
# Test configuration after modifying radio.liq
python3 test_radio_liq.py
```

### Monitoring System Health
```bash
# Regular health check
./test_radio_functional.sh
```

### Debugging Issues
```bash
# Detailed configuration analysis
python3 test_radio_liq.py

# Check specific system components
systemctl status ai-radio.service
docker logs ai-radio
journalctl -u ai-radio.service -f
```

## Test Maintenance

### Adding New Tests
1. Add test functions to appropriate test file
2. Update test documentation
3. Ensure test is idempotent (can run multiple times safely)
4. Add appropriate error handling

### Updating Tests After Configuration Changes
1. Review test coverage for new functionality
2. Update expected values in tests
3. Add tests for new features or endpoints
4. Update documentation

## Troubleshooting

### Test Environment Issues
- Ensure all required services are running
- Check firewall/network connectivity
- Verify file permissions on test scripts
- Ensure Python dependencies are installed

### False Positives/Negatives
- Some tests may show warnings for optional features
- Services may be transitioning during test execution
- Network latency can cause timeout failures
- Check actual system status if tests show unexpected results