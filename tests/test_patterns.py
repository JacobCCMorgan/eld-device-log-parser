"""One fixture per line family: the registry must classify each example into the intended
pattern and extract the named parameters."""
import pytest

from eldlog.patterns import PATTERNS, classify

CASES = [
    # body, expected pattern, expected params subset
    ("Send_data[5 byte]: 80 00 02 32 B0", "uart_tx", {"n_bytes": "5"}),
    ("Read_data[8 byte]: 06 03 80 50 89 00 01 5D", "uart_rx", {"n_bytes": "8"}),
    ("[BLEMessageGet]cmd: 50", "uart_cmd_ble", {"cmd": "50"}),
    ("[STMessageGet] cmd: 42", "uart_cmd_st", {"cmd": "42"}),
    ("[BLEMessageGet]Retcode: 0001", "uart_retcode_ble_get", {"retcode": "0001"}),
    ("[STGPSDataGet] Retcode: 0001", "uart_retcode", {"component": "STGPSDataGet", "retcode": "0001"}),
    ("[BLELedSet] set LED color start....", "op_start", {"component": "BLELedSet"}),
    ("[STECMDataGet] query ECM data start....", "op_start", {"component": "STECMDataGet"}),
    ("[WaitSTCmdReply] want cmd 41 timeout, use spilled FF ret=0005 for cmd-mismatch retry", "uart_wait_timeout", {"want": "41"}),
    ("[STBatteryCheck] ret/cmd=0005/FF want cmd 51, retry 1/3", "uart_mismatch_retry", {"component": "STBatteryCheck", "want": "51", "attempt": "1"}),
    ("ECMData: 00 00 0F 68 BD 4B", "decoded_frame", {"field": "ECMData"}),
    ("GPSValid: 01", "decoded_scalar", {"field": "GPSValid", "value": "01"}),
    ("[Driving][query ECM data] RPM:1188", "reading", {"state": "Driving", "group": "ECM data", "metric": "RPM", "value": "1188"}),
    ("[Sleeping][query GPS data] Latitude:36.35", "reading", {"state": "Sleeping", "metric": "Latitude", "value": "36.35"}),
    ("[Driving][query ECM data] Engine Hour:N/A", "reading", {"metric": "Engine Hour", "value": "N/A"}),
    ("[Sleeping][query ECM data] ECM data contains N/A or VIN not ready, retry (2/3)", "ecm_retry", {"state": "Sleeping", "attempt": "2", "max": "3"}),
    ("[Driving][query source mileage list] source mileage list ret=0001 SourceAddrLists=00,1009853.75,00,1009853.81", "source_list", {"ret": "0001"}),
    ("[Sleeping]/data free space: 86%", "free_space", {"state": "Sleeping", "value": "86"}),
    ("[Driving] timezone: -04:00", "tz", {"value": "-04:00"}),
    ("[Driving] driving mode flow start...", "flow_start", {"state": "Driving"}),
    ("[Standby] standby mode flow start...", "flow_start", {"state": "Standby"}),
    ("[Upgrade] update flow start...", "flow_start", {"state": "Upgrade"}),
    ("[Driving]GPS vehicle speed below 8 km/h and Speed and RPM are both N/A or 0, system will enter sleeping mode", "to_sleep", {}),
    ("[Sleeping]RPM greater than 0, system will enter driving mode", "to_drive", {}),
    ("[main]: boot, always standby (regardless of otaReturnMode)", "to_standby", {}),
    ("[main-ota] marked to enter OTA (GroupNo=7), main state machine will enter Upgrade", "to_upgrade", {}),
    ("[Sleeping] upload to backend", "upload_now", {"state": "Sleeping", "kind": "upload to backend"}),
    ("[Driving] EngineHour is N/A, compensated from file for upload 16432.45 (cumulative compensated seconds =30)", "compensate", {"field": "EngineHour", "value": "16432.45"}),
    ("[MQTT Publish]--- publish message >>:Topic:MProd/ELD/IMEI-0123456789", "mqtt_pub_topic", {"topic": "MProd/ELD/IMEI-0123456789"}),
    ('[MQTT Publish]Payload:{"mode":"2","timestamp":"20260923214150"}', "mqtt_pub_payload", {"json": '{"mode":"2","timestamp":"20260923214150"}'}),
    ("[MQTT messageHandler]--- received message <<:Topic:MProd/S/IMEI-0123456789/ack", "mqtt_rx_topic", {"topic": "MProd/S/IMEI-0123456789/ack"}),
    ('[MQTT messageHandler]Payload(MSG):{"timestamp": "20260923214701", "mode": "4", "is_stored": "0"}', "mqtt_rx_payload", {}),
    ("[MQTT messageHandler] forward topic=MProd/S/IMEI-0123456789/ack -> MQTTChannel (WaitForAck; expected topic=MProd/S/IMEI-0123456789/ack)", "mqtt_forward", {"channel": "MQTTChannel"}),
    ("[MQTT messageHandler] received regular ack topic=MProd/S/IMEI-0123456789/ack, forwarding to MQTTChannel", "mqtt_ack_regular", {}),
    ("[MQTT connectHandler]Connected", "mqtt_connected", {}),
    ("[MQTT reconnectHandler]DEV-0123456789--- auto reconnect after disconnect, consecutive disconnects:1", "mqtt_reconnect", {"count": "1"}),
    ("/work/iot/src/main/MQTT/mqtt.go:652: [MQTT lostHandler]blocker connection closed:read tcp <ip>-><ip>: read: connection reset by peer", "mqtt_lost", {"src_line": "652"}),
    ("[read][/config/USN]: 0015009", "config_rw", {"op": "read", "key": "USN", "value": "0015009"}),
    ("[write][/config/gpsLatitude]: 36.35", "config_rw", {"op": "write", "key": "gpsLatitude"}),
    ("[control LED][controlLED] start...", "led_control", {}),
    ("[RuntimeLED][channel] command: 2__LedY4__LedN0__01500__00500__0", "led_cmd", {"value": "2__LedY4__LedN0__01500__00500__0"}),
    ("[BleBroadcast] for 30 s not connected, re-enable BLE advertising", "ble_reenable", {"seconds": "30"}),
    ("[BLEStatusUpdate] received module status notification, responding", "ble_misc", {}),
    ("/work/iot/src/main/Controller/controller_driving.go:741: [Driving] got GPS Speed (39) but ECM field contains N/A, send ERR005", "err_event", {"gps_speed": "39", "code": "ERR005"}),
    ("[sendECMDeviceEvent] skip ERR005, cooldown 31m32.99843612s", "err_cooldown", {"code": "ERR005", "cooldown": "31m32.99843612s"}),
    ("/work/iot/src/main/Controller/controller_mqtt.go:962: [WaitForAck]Time out", "err_ack_timeout", {"src_line": "962"}),
    ("/work/iot/src/main/Lib/shell.go:45: [network check]error: exit status 1", "err_generic", {"message": "[network check]error: exit status 1"}),
    ("[SelfTest][device self-test]:4g fail", "selftest_fail", {"what": "4g"}),
    ("[NotifyECMEngineMileTimeout] Count=2 TimeoutIdx=1", "ecm_notify", {"component": "NotifyECMEngineMileTimeout"}),
    ("[SendDMS] backend no response, written to re-upload folder", "send_dms_fallback", {}),
    ("[Reupload] re-upload batch 1, total 1 records", "reupload_batch", {"batch": "1", "records": "1"}),
    ("[Reupload] re-upload /data/APP/upload/20260915_part2 done", "reupload_done", {"path": "/data/APP/upload/20260915_part2"}),
    ("[main] VERSION: v1.5.0", "boot_version", {"version": "v1.5.0"}),
    ("[main] System start...", "boot_start", {}),
    ("[Boot]ELD power-on boot flow start...", "boot_flow", {}),
    ("[Get4GInfor]RSRP value: -74dBm, len: 6", "modem_info", {"field": "RSRP", "value": "-74dBm"}),
    ("[Get4GInfor]IMEI value: IMEI-0123456789, len: 15", "modem_info", {"field": "IMEI"}),
    ("[init]:14.547247859s", "init_timing", {"seconds": "14.547247859"}),
    ("[SelfTest]:219.5575ms", "selftest_timing", {"ms": "219.5575", "unit": "ms"}),
    ("[SelfTest] device self-test start...", "selftest", {}),
    ("[International API Send] response time: 872.411458ms", "api_latency", {"value": "872.411458", "unit": "ms"}),
    ("[restore.sh] /data/APP/upgrade/ md5 is same, skip.", "shell", {}),
    ("SIMSWAP and set <carrier> APN start...", "simswap", {}),
    ("[Upgrade]UPS Output:Success", "ota", {}),
    ("[CompressLog] Log date filter done", "housekeeping", {}),
    ("[main] 23 MiB /data/APP/logs/sys.log", "housekeeping", {}),
    ("[Driving][UD] entering unidentified driving", "ud", {}),
    ("***************************************************", "rule", {}),
    ("", "blank", {}),
]


@pytest.mark.parametrize("body,expected,params", CASES, ids=[c[1] + ":" + c[0][:30] for c in CASES])
def test_classify(body, expected, params):
    name, cat, got = classify(body)
    assert name == expected, (name, cat, got)
    for k, v in params.items():
        assert got.get(k) == v, (k, got)


def test_unknown_line_is_other():
    assert classify("something the firmware never says 12345")[0] == "other"


def test_pattern_names_unique():
    names = [n for n, _, _ in PATTERNS]
    assert len(names) == len(set(names))


def test_every_pattern_has_a_fixture():
    covered = {c[1] for c in CASES}
    missing = sorted({n for n, _, _ in PATTERNS} - covered - {"banner", "op_value", "uart_coalesced", "mqtt_ack_resend", "mqtt_subscribe", "mqtt_subscribe_hdr", "mqtt_config",
                                                              "mqtt_create", "ota_ack_topic", "led_set", "reupload_check", "api_body", "api_misc", "server_time", "init_misc",
                                                              "config_dump", "ota_attempt", "not_ready_retry", "not_ready_giveup"})
    assert not missing, missing
