"""Line-pattern registry.

Each entry is (name, category, compiled regex). Patterns are tried in order and
the first match wins; named groups become the line's parameters. Categories
group the ~400 message templates into a dozen families that the tables in
`tables.py` are built from:

    uart        byte dumps and command/retcode lines on the two serial buses
                (BLE module, ST sensor/ECM micro-controller)
    telemetry   per-cycle sensor readings tagged with the state machine state
    state       state-machine flow starts and transition reasons
    mqtt        publish / ack / subscribe / connection events
    config      reads and writes of /config/* keys
    led         LED control chatter
    ble         BLE advertising / device-name housekeeping
    error       Error-level lines, ERR event emission and cooldown skips,
                timeouts, retries
    upload      offline buffering and re-upload
    boot        power-on, init, modem info, self-test, time sync, login
    ota         over-the-air update flow
    housekeeping log compression / rotation / disk usage
    ud          unidentified-driving handling
    separator   decorative lines and blank lines
    other       everything else (counted as unparsed in coverage)
"""
from __future__ import annotations

import re

HEX = r"(?:[0-9A-Fa-fx]{2} ?)+"
STATE = r"(?P<state>Standby|Sleeping|Driving|Upgrade|main|Boot)"

_P: list[tuple[str, str, str]] = [
    # ---- separators
    ("blank", "separator", r"^\s*$"),
    ("rule", "separator", r"^[*=\-]{10,}$"),
    ("banner", "separator", r"^\*{5,}[A-Za-z.]+\*{5,}$"),
    # ---- uart byte dumps
    ("uart_tx", "uart", rf"^Send_data\[(?P<n_bytes>\d+) byte\]: (?P<bytes>{HEX})$"),
    ("uart_rx", "uart", rf"^Read_data\[(?P<n_bytes>\d+) byte\]: (?P<bytes>{HEX})$"),
    ("uart_cmd_ble", "uart", r"^\[BLEMessageGet\]\s*cmd: (?P<cmd>[0-9A-F]{2})$"),
    ("uart_cmd_st", "uart", r"^\[STMessageGet\]\s*cmd: (?P<cmd>[0-9A-F]{2})$"),
    ("uart_retcode_ble_get", "uart", r"^\[BLEMessageGet\]\s*Retcode: (?P<retcode>[0-9A-F]{4})$"),
    ("uart_retcode", "uart", r"^\[(?P<component>[A-Za-z0-9]+)\]\s*Retcode: (?P<retcode>[0-9A-F]{4})$"),
    ("uart_coalesced", "uart", r"^\[BLEMessageGet\] UART read contained (?P<frames>\d+) frames"),
    ("uart_mismatch_retry", "error", r"^\[(?P<component>[A-Za-z0-9]+)\] ret/cmd=(?P<ret>[0-9A-F]+)/(?P<got>[0-9A-F]+) want cmd (?P<want>[0-9A-F]+), retry (?P<attempt>\d+)/(?P<max>\d+)"),
    ("uart_wait_timeout", "error", r"^\[WaitSTCmdReply\] want cmd (?P<want>[0-9A-F]+) timeout"),
    ("decoded_frame", "uart", r"^(?P<field>ECMData|GPSData|BatteryData|fwVersion): (?P<value>.+)$"),
    ("decoded_scalar", "uart", r"^(?P<field>ECMProtocol|GPSValid|BatteryCheck|WakeupReason): (?P<value>\S+)$"),
    ("op_start", "uart", r"^\[(?P<component>[A-Za-z0-9]+)\]\s*(?P<what>[^:\[]*?) ?start\.{3,}$"),
    ("op_value", "uart", r"^\[(?P<component>BLETemperatureGet|BLEFWVersionGet|BLEDeviceNameGet|BLEDeviceNameSet|TestModeGet|SourceMileageListGet|STBattery12VCheck|STBatteryCheck)\]\s*(?P<field>[A-Za-z]+): ?(?P<value>.*)$"),
    # ---- telemetry readings tagged with the state
    ("ecm_retry", "telemetry", rf"^\[{STATE}\]\[query ECM data\] ECM data contains N/A or VIN not ready, retry \((?P<attempt>\d+)/(?P<max>\d+)\)"),
    ("source_list", "telemetry", rf"^\[{STATE}\]\[query source mileage list\] source mileage list ret=(?P<ret>[0-9A-F]+) SourceAddrLists=(?P<value>.*)$"),
    ("reading", "telemetry", rf"^\[{STATE}\]\[query (?P<group>[A-Za-z0-9 ]+)\] ?(?P<metric>[A-Za-z0-9 ()/]+?):\s*(?P<value>.*)$"),
    ("free_space", "telemetry", rf"^\[{STATE}\]/data free space: (?P<value>\d+)%$"),
    ("tz", "telemetry", rf"^\[{STATE}\] timezone: (?P<value>[+-]?\d{{2}}:\d{{2}})$"),
    # ---- state machine
    ("flow_start", "state", r"^\[(?P<state>Standby|Sleeping|Driving|Upgrade)\] (?:standby|sleeping|driving|update) mode flow start|^\[(?P<state2>Upgrade)\] update flow start"),
    ("to_sleep", "state", r"^\[Driving\].*system will enter sleeping mode"),
    ("to_drive", "state", r"^\[Sleeping\].*system will enter driving mode"),
    ("to_standby", "state", r"^\[main\]: .*always standby|^\[OTA\] ignore otaReturnMode.*standby"),
    ("to_upgrade", "state", r"^\[main-ota\] marked to enter OTA|^\[Sleeping\] remote OTA marked to enter mode"),
    ("ota_attempt", "state", r"^\[Driving\] Speed=.*attempt OTA flow"),
    ("upload_now", "state", rf"^\[{STATE}\] (?P<kind>upload to backend before sleep|upload status to backend|upload to backend|before update, upload to backend|update done, upload to backend)$"),
    ("compensate", "state", rf"^\[{STATE}\] (?P<field>EngineHour|EngineMile) is N/A, compensated from file for upload (?P<value>[\d.]+)"),
    ("not_ready_retry", "state", r"^\[Standby\] before first upload GPS/ECM not ready \(GPSValid=(?P<gps_valid>\d+) ECMProtocol=(?P<ecm_protocol>\d+)\), (?P<wait_s>\d+) s then retry \((?P<attempt>\d+)/(?P<max>\d+)\)"),
    ("not_ready_giveup", "state", r"^\[Standby\] before first upload GPS/ECM retry"),
    # ---- MQTT
    ("mqtt_pub_topic", "mqtt", r"^\[MQTT Publish\]--- publish message >>:Topic:(?P<topic>\S+)$"),
    ("mqtt_pub_payload", "mqtt", r"^\[MQTT Publish\]Payload:(?P<json>\{.*\})$"),
    ("mqtt_rx_topic", "mqtt", r"^\[MQTT messageHandler\]--- received message <<:Topic:(?P<topic>\S+)$"),
    ("mqtt_rx_payload", "mqtt", r"^\[MQTT messageHandler\]Payload\(MSG\):(?P<json>\{.*\})$"),
    ("mqtt_forward", "mqtt", r"^\[MQTT messageHandler\] forward topic=(?P<topic>\S+) -> (?P<channel>\w+)"),
    ("mqtt_ack_regular", "mqtt", r"^\[MQTT messageHandler\] received regular ack topic=(?P<topic>\S+)"),
    ("mqtt_ack_resend", "mqtt", r"^\[MQTT messageHandler\] received re-upload ack topic=(?P<topic>\S+)"),
    ("mqtt_connected", "mqtt", r"^\[MQTT connectHandler\]Connected$"),
    ("mqtt_subscribe", "mqtt", r"^\[MQTT connectHandler\] subscribe topic: (?P<topic>\S+)$"),
    ("mqtt_subscribe_hdr", "mqtt", r"^\[MQTT connectHandler\]--- subscribe topics after connect:$"),
    ("mqtt_reconnect", "mqtt", r"^\[MQTT reconnectHandler\](?P<client>\S+)--- auto reconnect after disconnect, consecutive disconnects:(?P<count>\d+)"),
    ("mqtt_lost", "error", r"^(?P<src>/\S+\.go):(?P<src_line>\d+): \[MQTT lostHandler\](?P<message>.*)$"),
    ("mqtt_config", "mqtt", r"^(?P<field>Url|KeepAliveTime|PingTimeoutTime|Sub|Pub): (?P<value>.*)$"),
    ("mqtt_create", "mqtt", r"^\[(?:CreateMQTT|EnsureMQTT|HasUsableMQTTCredentials|GetMQTTConfigJson)\]"),
    ("ota_ack_topic", "mqtt", r"^\[MQTT Publish\]--- publish message >>:Topic:(?P<topic>\S+/ota/ack)$"),
    # ---- config IO
    ("config_rw", "config", r"^\[(?P<op>read|write)\]\[/config/(?P<key>[A-Za-z0-9_]+)\]:\s?(?P<value>.*)$"),
    # ---- LED
    ("led_control", "led", r"^\[control LED\]\[controlLED\] start"),
    ("led_cmd", "led", r"^\[RuntimeLED\]\[channel\] command: (?P<value>\S+)$"),
    ("led_set", "led", r"^\[BLELedSet\]"),
    # ---- BLE housekeeping
    ("ble_reenable", "ble", r"^\[BleBroadcast\] for (?P<seconds>\d+) s not connected, re-enable BLE advertising"),
    ("ble_misc", "ble", r"^\[(?:BleBroadcast|BLEStatusUpdate|BLEStatusUpdateSet|BLEAdvertisSetting|BLEAdvertisOnOff|BLEDeviceNameSet|BLEDeviceNameGet|BLERoleSetting|enable BLE advertising|set role ID)\]"),
    # ---- errors and events
    ("err_event", "error", r"^(?P<src>/\S+\.go):(?P<src_line>\d+): \[Driving\] got GPS Speed \((?P<gps_speed>\d+)\) but ECM field contains N/A, send (?P<code>ERR\d+)"),
    ("err_cooldown", "error", r"^\[sendECMDeviceEvent\] skip (?P<code>ERR\d+), cooldown (?P<cooldown>\S+)"),
    ("err_ack_timeout", "error", r"^(?P<src>/\S+\.go):(?P<src_line>\d+): \[WaitForAck\]Time out"),
    ("err_generic", "error", r"^(?P<src>/\S+\.go):(?P<src_line>\d+): (?P<message>.*)$"),
    ("selftest_fail", "error", r"^\[Driving\] self-test failed|^\[SelfTest\]\[device self-test\]:(?P<what>\w+) fail"),
    ("ecm_notify", "error", r"^\[(?P<component>NotifyECMNoDetected|NotifyECMAutoNoDetected|NotifyECMEngineMileTimeout)\]"),
    # ---- offline buffering
    ("send_dms_fallback", "upload", r"^\[SendDMS\] backend no response, written to re-upload folder"),
    ("reupload_check", "upload", r"^\[Reupload\] check re-upload file: (?P<path>\S+)"),
    ("reupload_batch", "upload", r"^\[Reupload\] re-upload batch (?P<batch>\d+), total (?P<records>\d+) records"),
    ("reupload_done", "upload", r"^\[Reupload\] re-upload (?P<path>\S+) done"),
    # ---- boot and init
    ("boot_version", "boot", r"^\[main\] VERSION:\s*(?P<version>\S+)$"),
    ("boot_start", "boot", r"^\[main\] System start"),
    ("boot_flow", "boot", r"^\[Boot\]ELD power-on boot flow start"),
    ("modem_info", "boot", r"^\[Get4GInfor\](?P<field>IMEI|ICCID|RSRP|RSRQ|SNR|NetType|Connect)(?: value)?\s*: (?P<value>[^,]+)"),
    ("init_timing", "boot", r"^\[init\]:(?P<seconds>[\d.]+)s$"),
    ("selftest_timing", "boot", r"^\[SelfTest\]:(?P<ms>[\d.]+)(?P<unit>ms|s)$"),
    ("selftest", "boot", r"^\[SelfTest\]"),
    ("api_latency", "boot", r"^\[International API Send\] response time: (?P<value>[\d.]+)(?P<unit>ms|s)$"),
    ("api_body", "boot", r"^\[International API Send\] Response Body:"),
    ("api_misc", "boot", r"^\[(?:International API|Login|GetTime|GetInfo|time sync)\]"),
    ("server_time", "boot", r"^Server time: (?P<value>\d+)$"),
    ("init_misc", "boot", r"^\[(?:init|init start|main|NewRS232|TestModeGet|SourceAddressSet|STWakeupGet|CarInfoGet|network check|FreeMem)\]"),
    ("config_dump", "boot", r"^(?:Console:ON|LogSizek: \d+|LogTar: \d+|Rs232ST:\S+|Rs232BLE:\S+)$"),
    ("shell", "boot", r"^\[(?:restore|change_user|rc)\.sh\]"),
    ("simswap", "boot", r"^SIMSWAP"),
    # ---- OTA
    ("ota", "ota", r"^\[(?:main-ota|Upgrade|OTA)\]|^run: /data/APP/upgrade"),
    # ---- housekeeping
    ("housekeeping", "housekeeping", r"^\[(?:CompressLog|CleanOldDateFiles)\]|^\[(?:main|Sleeping|Standby)\] (?:clear tarlogs|Log compression ok|log rotation done)|^\[main\]\s+\d+ [KM]iB |^\[main\] /data|^\[main\]/data main usage"),
    # ---- unidentified driving
    ("ud", "ud", r"^\[Driving\]\[UD\]"),
]

PATTERNS: list[tuple[str, str, re.Pattern]] = [(n, c, re.compile(r)) for n, c, r in _P]
CATEGORIES = sorted({c for _, c, _ in _P} | {"other"})


def classify(body: str) -> tuple[str, str, dict]:
    """Return (pattern name, category, params) for a log body; ('other','other',{}) when unmatched."""
    for name, cat, rx in PATTERNS:
        m = rx.match(body)
        if m:
            params = {k: v for k, v in m.groupdict().items() if v is not None}
            if name == "flow_start" and "state2" in params:
                params["state"] = params.pop("state2")
            return name, cat, params
    return "other", "other", {}
