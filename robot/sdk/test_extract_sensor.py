def extract_sensors(data):
    entity_names = []
    for area in data:
        for area_name, devices in area.items():
            for device in devices:
                for device_name, entities in device.items():
                    for entity in entities:
                        for entity_name in entity.keys():
                            entity_names.append(entity_name)
    return entity_names

# json_data = [{"办公室":[{"sensor.xiaomi_mc5_1642_electricity":[]},{"switch.xiaomi_mc5_1642_switch_status":[]},{"switch.xiaomi_mc5_1642_heater_2":[]},{"switch.xiaomi_mc5_1642_alarm":[]},{"sensor.xiaomi_mc5_1642_temperature":[]},{"light.xiaomi_mc5_1642_indicator_light":[]},{"switch.xiaomi_mc5_1642_eco":[]},{"switch.xiaomi_mc5_1642_dryer":[]},{"switch.xiaomi_mc5_1642_sleep_mode":[]},{"climate.xiaomi_mc5_1642_air_conditioner":[{"turn_on":[]},{"turn_off":[]},{"toggle":[]},{"set_hvac_mode":["cool","heat","dry","fan_only","auto","off"]},{"set_fan_mode":["auto","level1","level2","level3","level4","level5","level6","level7"]},{"set_swing_mode":["off","vertical"]}]}]}]
json_data= [{'办公室': [{'财富广场门锁': [{"sensor.xiaomi_mc5_1642_electricity":[]},{"switch.xiaomi_mc5_1642_switch_status":[]},{"switch.xiaomi_mc5_1642_heater_2":[]},{"switch.xiaomi_mc5_1642_alarm":[]},{"sensor.xiaomi_mc5_1642_temperature":[]},{"light.xiaomi_mc5_1642_indicator_light":[]},{"switch.xiaomi_mc5_1642_eco":[]},{"switch.xiaomi_mc5_1642_dryer":[]},{"switch.xiaomi_mc5_1642_sleep_mode":[]},{"climate.xiaomi_mc5_1642_air_conditioner":[{"turn_on":[]},{"turn_off":[]},{"toggle":[]},{"set_hvac_mode":["cool","heat","dry","fan_only","auto","off"]},{"set_fan_mode":["auto","level1","level2","level3","level4","level5","level6","level7"]},{"set_swing_mode":["off","vertical"]}]}]}]}]
sensors_list = extract_sensors(json_data)
print(sensors_list)
