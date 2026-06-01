/*
 * HTTP-сервер подсветки для АОИ-Web (docs/esp32_lighting_api.md).
 * POST /api/lighting/control — preset, brightness (0–100), color (#RRGGBB).
 */
#include <WiFi.h>
#include <WebServer.h>

#ifndef WIFI_SSID
#define WIFI_SSID "your-ssid"
#endif
#ifndef WIFI_PASS
#define WIFI_PASS "your-password"
#endif

WebServer server(80);

String activePreset = "off";
int activeBrightness = 80;
String activeColor = "#ffffff";

void applyLighting() {
  // TODO: WS2811/NeoPixel — preset + brightness + RGB из activeColor
  Serial.printf("preset=%s brightness=%d color=%s\n",
                activePreset.c_str(), activeBrightness, activeColor.c_str());
}

bool parseHexColor(const String& hex, uint8_t& r, uint8_t& g, uint8_t& b) {
  String s = hex;
  if (s.startsWith("#")) s = s.substring(1);
  if (s.length() != 6) return false;
  r = (uint8_t) strtol(s.substring(0, 2).c_str(), NULL, 16);
  g = (uint8_t) strtol(s.substring(2, 4).c_str(), NULL, 16);
  b = (uint8_t) strtol(s.substring(4, 6).c_str(), NULL, 16);
  return true;
}

void handleHealth() {
  server.send(200, "application/json", "{\"ok\":true,\"device\":\"esp32-aoi\"}");
}

void handleControl() {
  if (!server.hasArg("plain")) {
    server.send(400, "application/json", "{\"error\":\"empty body\"}");
    return;
  }
  String body = server.arg("plain");

  if (body.indexOf("white_diffuse") >= 0) activePreset = "white_diffuse";
  else if (body.indexOf("rgb_highlight") >= 0) activePreset = "rgb_highlight";
  else if (body.indexOf("\"off\"") >= 0 || body.indexOf(":off") >= 0) activePreset = "off";

  int bi = body.indexOf("\"brightness\"");
  if (bi >= 0) {
    int colon = body.indexOf(':', bi);
    if (colon >= 0) {
      int val = body.substring(colon + 1).toInt();
      activeBrightness = constrain(val, 0, 100);
    }
  }

  int ci = body.indexOf("\"color\"");
  if (ci >= 0) {
    int q1 = body.indexOf('#', ci);
    if (q1 >= 0 && q1 + 7 <= body.length()) {
      activeColor = body.substring(q1, q1 + 7);
      activeColor.toLowerCase();
    }
  }

  applyLighting();
  server.send(204);
}

void setup() {
  Serial.begin(115200);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  while (WiFi.status() != WL_CONNECTED) delay(500);
  Serial.println(WiFi.localIP());

  server.on("/health", HTTP_GET, handleHealth);
  server.on("/api/lighting/control", HTTP_POST, handleControl);
  server.on("/api/lighting/preset", HTTP_POST, handleControl);
  server.begin();
}

void loop() {
  server.handleClient();
}
