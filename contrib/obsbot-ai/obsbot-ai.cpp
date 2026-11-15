// obsbot-ai: drive an OBSBOT camera's onboard AI tracking and stored settings over OBSBOT's SDK (libdev).
// Optional helper for CamChord; prints one JSON line on stdout. Tested with the Tiny 2 Lite.
//   obsbot-ai status
//   obsbot-ai mode none|group|human|hand|whiteboard|desk [normal|upper|closeup|headless|lower]
//   obsbot-ai framing standard|headroom|motion
//   obsbot-ai autozoom on|off
//   obsbot-ai wake
//   obsbot-ai attitude          true gimbal angles (v4l2 only reports the last target)
//   obsbot-ai gesture target|zoom|dynamic on|off   turn one hand gesture on or off
//   obsbot-ai mirror on|off     flip the picture left-right
//   obsbot-ai bootcenter        when plugged in: centered, zoom 1x, no tracking
//   (mode also sets AI auto zoom: on for tracking modes, off for none)
// Exit 0 with {"ok":true,...}; on failure {"ok":false,"error":...} and exit 1.
#include <dev/devs.hpp>
#include <chrono>
#include <cstring>
#include <iostream>
#include <string>
#include <thread>
using namespace std;

static const char *MODES[] = {"none", "group", "human", "hand", "whiteboard", "desk", "switching"};
static const char *SUBS[] = {"normal", "upper", "closeup", "headless", "lower"};
static const char *FRAMES[] = {"standard", "headroom", "motion"};

static int idx(const char *const *names, int n, const string &s) {
    for (int i = 0; i < n; i++) if (s == names[i]) return i;
    return -1;
}
static int fail(const string &e) { cout << "{\"ok\":false,\"error\":\"" << e << "\"}" << endl; return 1; }

static int status(Device &d) {
    Device::CameraStatus st; memset(&st, 0, sizeof st);
    if (d.cameraGetCameraStatusU(st) != RM_RET_OK) return fail("status read failed");
    Device::AiStatus ai{};
    bool aiOk = d.aiGetAiStatusR(&ai) == RM_RET_OK;
    int m = st.tiny.ai_mode, s = st.tiny.ai_sub_mode;
    int v = aiOk ? (int)ai.v_track_landscape : -1;
    cout << "{\"ok\":true"
         << ",\"mode\":\"" << (m >= 0 && m <= 6 ? MODES[m] : "unknown") << "\""
         << ",\"sub\":\"" << (s >= 0 && s <= 4 ? SUBS[s] : "unknown") << "\""
         << ",\"target\":" << (st.tiny.ai_target ? "true" : "false")
         << ",\"devStatus\":" << (int)st.tiny.dev_status
         << ",\"zoom\":" << (int)st.tiny.zoom_ratio
         << ",\"framing\":\"" << (v >= 0 && v <= 2 ? FRAMES[v] : "unknown") << "\""
         << ",\"gestureTarget\":" << (aiOk && ai.gesture_target ? "true" : "false")
         << ",\"gestureZoom\":" << (aiOk && ai.gesture_zoom ? "true" : "false")
         << ",\"gestureDynamicZoom\":" << (aiOk && ai.gesture_dynamic_zoom ? "true" : "false")
         << ",\"mirror\":" << (st.tiny.image_flip_hor ? "true" : "false")
         << ",\"bootMode\":" << (int)st.tiny.boot_mode
         << "}" << endl;
    return 0;
}

int main(int argc, char **argv) {
    if (argc < 2) return fail("usage: obsbot-ai status|mode|framing|autozoom|wake");
    string c = argv[1], a1 = argc > 2 ? argv[2] : "", a2 = argc > 3 ? argv[3] : "";
    bool conn = false;
    Devices::get().setDevChangedCallback([&](string, bool up, void *) { if (up) conn = true; }, nullptr);
    Devices::get().setEnableMdnsScan(false);
    for (int i = 0; i < 100 && !conn; i++) this_thread::sleep_for(chrono::milliseconds(100));
    this_thread::sleep_for(chrono::milliseconds(300));
    auto list = Devices::get().getDevList();
    if (list.empty()) return fail("camera not found");
    Device &d = *list.front();
    int r = RM_RET_OK;
    if (c == "status") return status(d);
    if (c == "attitude") {
        float xyz[3] = {0, 0, 0};
        if (d.gimbalGetAttitudeInfoR(xyz) != RM_RET_OK) return fail("attitude read failed");
        cout << "{\"ok\":true,\"roll\":" << xyz[0] << ",\"pitch\":" << xyz[1] << ",\"pan\":" << xyz[2] << "}" << endl;
        return 0;
    }
    if (c == "mode") {
        int m = idx(MODES, 6, a1);
        if (m < 0) return fail("bad mode");
        int s = a2.empty() ? 0 : idx(SUBS, 5, a2);
        if (s < 0) return fail("bad sub-mode");
        r = d.cameraSetAiModeU((Device::AiWorkModeType)m, m == Device::AiWorkModeHuman ? s : 0);
        // Auto zoom follows tracking: on while tracking, off otherwise.
        if (r == RM_RET_OK) { this_thread::sleep_for(chrono::milliseconds(300)); d.aiSetAiAutoZoomR(m != 0); }
    } else if (c == "framing") {
        int v = idx(FRAMES, 3, a1);
        if (v < 0) return fail("bad framing");
        r = d.aiSetTrackingModeR((Device::AiVerticalTrackType)v);
    } else if (c == "autozoom") {
        if (a1 != "on" && a1 != "off") return fail("autozoom on|off");
        r = d.aiSetAiAutoZoomR(a1 == "on");
    } else if (c == "gesture") {
        int g = a1 == "target" ? 0 : a1 == "zoom" ? 1 : a1 == "dynamic" ? 2 : -1;
        if (g < 0 || (a2 != "on" && a2 != "off")) return fail("gesture target|zoom|dynamic on|off");
        r = d.aiSetGestureCtrlIndividualR(g, a2 == "on");
    } else if (c == "mirror") {
        if (a1 != "on" && a1 != "off") return fail("mirror on|off");
        r = d.cameraSetImageFlipHorizonU(a1 == "on" ? 1 : 0);
    } else if (c == "bootcenter") {
        Device::PresetPosInfo p{}; p.id = 0; p.roll = 0; p.pitch = 0; p.yaw = 0; p.zoom = 1.0f;
        r = d.aiSetGimbalBootPosR(p);
        if (r == RM_RET_OK) r = d.cameraSetBootModeU(Device::AiWorkModeNone, Device::AiSubModeNormal);
    } else if (c == "wake") {
        r = d.cameraSetDevRunStatusR(Device::DevStatusRun);
    } else return fail("unknown command");
    if (r != RM_RET_OK) return fail(c + " rejected by camera (" + to_string(r) + ")");
    this_thread::sleep_for(chrono::milliseconds(800));
    return status(d);
}
