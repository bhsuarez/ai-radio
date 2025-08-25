#!/usr/bin/env python3
"""
Test suite for radio.liq Liquidsoap configuration
Tests configuration syntax, service connectivity, and key functionality
"""

import subprocess
import socket
import time
import json
import os
import sys
import requests
from pathlib import Path

class RadioLiqTest:
    def __init__(self):
        self.liquidsoap_config = "/opt/ai-radio/radio.liq"
        self.telnet_port = 1234
        self.icecast_port = 8000
        self.harbor_port = 8001
        self.flask_port = 5055
        self.test_results = []
        
    def log_test(self, test_name, passed, message=""):
        """Log test result"""
        status = "PASS" if passed else "FAIL"
        self.test_results.append({
            "test": test_name,
            "status": status,
            "message": message
        })
        print(f"[{status}] {test_name}: {message}")
        
    def test_liquidsoap_syntax(self):
        """Test if radio.liq has valid Liquidsoap syntax"""
        try:
            # Use liquidsoap --check-lib to validate syntax
            result = subprocess.run(
                ["liquidsoap", "--check-lib", self.liquidsoap_config],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode == 0:
                self.log_test("Liquidsoap Syntax Check", True, "Configuration syntax is valid")
            else:
                self.log_test("Liquidsoap Syntax Check", False, f"Syntax error: {result.stderr}")
                
        except subprocess.TimeoutExpired:
            self.log_test("Liquidsoap Syntax Check", False, "Timeout during syntax check")
        except FileNotFoundError:
            self.log_test("Liquidsoap Syntax Check", False, "liquidsoap command not found")
        except Exception as e:
            self.log_test("Liquidsoap Syntax Check", False, f"Error: {str(e)}")
            
    def test_required_files(self):
        """Test if required files exist"""
        required_files = [
            "/opt/ai-radio/library_clean.m3u",
            "/opt/ai-radio/dj_settings.json",
        ]
        
        all_exist = True
        missing_files = []
        
        for file_path in required_files:
            if not os.path.exists(file_path):
                all_exist = False
                missing_files.append(file_path)
                
        if all_exist:
            self.log_test("Required Files Check", True, "All required files exist")
        else:
            self.log_test("Required Files Check", False, f"Missing files: {', '.join(missing_files)}")
            
    def test_port_accessibility(self):
        """Test if configured ports are accessible"""
        ports_to_test = [
            (self.telnet_port, "Telnet Control"),
            (self.icecast_port, "Icecast Stream"),
            (self.harbor_port, "Harbor Input"),
            (self.flask_port, "Flask API")
        ]
        
        for port, description in ports_to_test:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(2)
                result = sock.connect_ex(('127.0.0.1', port))
                sock.close()
                
                if result == 0:
                    self.log_test(f"Port {port} ({description})", True, "Port is accessible")
                else:
                    self.log_test(f"Port {port} ({description})", False, "Port is not accessible")
                    
            except Exception as e:
                self.log_test(f"Port {port} ({description})", False, f"Error testing port: {str(e)}")
                
    def test_telnet_commands(self):
        """Test basic telnet commands if telnet service is running"""
        try:
            # Test basic telnet connectivity
            import telnetlib
            tn = telnetlib.Telnet('127.0.0.1', self.telnet_port, timeout=5)
            
            # Test help command
            tn.write(b'help\n')
            response = tn.read_until(b'END', timeout=5)
            
            if b'help' in response or b'Available commands' in response:
                self.log_test("Telnet Commands", True, "Telnet interface responds to commands")
            else:
                self.log_test("Telnet Commands", False, "Telnet interface not responding properly")
                
            tn.close()
            
        except ImportError:
            self.log_test("Telnet Commands", False, "telnetlib not available")
        except Exception as e:
            self.log_test("Telnet Commands", False, f"Cannot connect to telnet: {str(e)}")
            
    def test_stream_endpoint(self):
        """Test if the stream endpoint is accessible"""
        try:
            response = requests.head(f"http://127.0.0.1:{self.icecast_port}/stream.mp3", timeout=5)
            
            if response.status_code == 200:
                self.log_test("Stream Endpoint", True, "Stream is accessible")
            else:
                self.log_test("Stream Endpoint", False, f"Stream returned status code: {response.status_code}")
                
        except requests.exceptions.RequestException as e:
            self.log_test("Stream Endpoint", False, f"Cannot access stream: {str(e)}")
            
    def test_flask_api_endpoints(self):
        """Test Flask API endpoints referenced in the config"""
        endpoints_to_test = [
            "/api/event",
            "/api/dj-now"
        ]
        
        for endpoint in endpoints_to_test:
            try:
                response = requests.get(f"http://127.0.0.1:{self.flask_port}{endpoint}", timeout=5)
                
                # Accept various response codes as valid (200, 400, 404, 405 are all valid responses)
                if response.status_code in [200, 400, 404, 405]:
                    self.log_test(f"Flask API {endpoint}", True, f"Endpoint responds (status: {response.status_code})")
                else:
                    self.log_test(f"Flask API {endpoint}", False, f"Unexpected status code: {response.status_code}")
                    
            except requests.exceptions.RequestException as e:
                self.log_test(f"Flask API {endpoint}", False, f"Cannot access endpoint: {str(e)}")
                
    def test_tts_scripts(self):
        """Test if TTS generation scripts exist and are executable"""
        tts_scripts = [
            "/opt/ai-radio/dj_enqueue_xtts.sh",
            "/opt/ai-radio/dj_enqueue_xtts_ai.sh",
            "/opt/ai-radio/gen_ai_dj_line_enhanced.sh"
        ]
        
        all_scripts_ok = True
        issues = []
        
        for script in tts_scripts:
            if not os.path.exists(script):
                all_scripts_ok = False
                issues.append(f"{script} does not exist")
            elif not os.access(script, os.X_OK):
                all_scripts_ok = False
                issues.append(f"{script} is not executable")
                
        if all_scripts_ok:
            self.log_test("TTS Scripts", True, "All TTS scripts exist and are executable")
        else:
            self.log_test("TTS Scripts", False, f"Issues: {'; '.join(issues)}")
            
    def test_configuration_values(self):
        """Test critical configuration values in radio.liq"""
        try:
            with open(self.liquidsoap_config, 'r') as f:
                config_content = f.read()
                
            # Check for critical settings
            checks = [
                ("settings.server.telnet := true", "Telnet server enabled"),
                ("port=1234", "Telnet port configured"),
                ("port=8000", "Icecast port configured"),
                ("port=8001", "Harbor port configured"),
                ("timeout=30.0", "TTS queue timeout set"),
                ("track_sensitive=true", "Track-sensitive fallback configured"),
                ("reload=300", "Playlist reload interval set")
            ]
            
            all_checks_passed = True
            failed_checks = []
            
            for check, description in checks:
                if check in config_content:
                    self.log_test(f"Config: {description}", True, "Setting found")
                else:
                    all_checks_passed = False
                    failed_checks.append(description)
                    self.log_test(f"Config: {description}", False, "Setting not found")
                    
        except Exception as e:
            self.log_test("Configuration Values", False, f"Error reading config: {str(e)}")
            
    def test_metadata_functions(self):
        """Test if metadata handling functions are properly defined"""
        try:
            with open(self.liquidsoap_config, 'r') as f:
                config_content = f.read()
                
            required_functions = [
                "meta_get",
                "send_metadata_update", 
                "auto_generate_dj_intro",
                "announce_song",
                "update_metadata"
            ]
            
            all_functions_found = True
            missing_functions = []
            
            for func in required_functions:
                if f"def {func}" in config_content:
                    self.log_test(f"Function: {func}", True, "Function defined")
                else:
                    all_functions_found = False
                    missing_functions.append(func)
                    self.log_test(f"Function: {func}", False, "Function not found")
                    
        except Exception as e:
            self.log_test("Metadata Functions", False, f"Error checking functions: {str(e)}")
            
    def run_all_tests(self):
        """Run all tests and provide summary"""
        print("=" * 60)
        print("AI Radio Liquidsoap Configuration Test Suite")
        print("=" * 60)
        
        # Run tests
        self.test_liquidsoap_syntax()
        self.test_required_files()
        self.test_configuration_values()
        self.test_metadata_functions()
        self.test_tts_scripts()
        self.test_port_accessibility()
        self.test_telnet_commands()
        self.test_stream_endpoint()
        self.test_flask_api_endpoints()
        
        # Summary
        print("\n" + "=" * 60)
        print("TEST SUMMARY")
        print("=" * 60)
        
        passed = len([r for r in self.test_results if r["status"] == "PASS"])
        failed = len([r for r in self.test_results if r["status"] == "FAIL"])
        total = len(self.test_results)
        
        print(f"Total tests: {total}")
        print(f"Passed: {passed}")
        print(f"Failed: {failed}")
        print(f"Success rate: {(passed/total)*100:.1f}%")
        
        if failed > 0:
            print(f"\nFAILED TESTS:")
            for result in self.test_results:
                if result["status"] == "FAIL":
                    print(f"  - {result['test']}: {result['message']}")
                    
        return failed == 0

if __name__ == "__main__":
    tester = RadioLiqTest()
    success = tester.run_all_tests()
    sys.exit(0 if success else 1)