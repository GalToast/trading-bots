import json, sys

registry_path = "C:/Users/HP/Desktop/Temp while my comp is at the shop/trading-bots/configs/penetration_lattice_runner_registry.json"
config_paths = [
    "C:/Users/HP/Desktop/Temp while my comp is at the shop/trading-bots/configs/hungry_hippo_ethusd_m5_step3p0_retuned_shadow.json",
    "C:/Users/HP/Desktop/Temp while my comp is at the shop/trading-bots/configs/hungry_hippo_ethusd_m5_step5_shadow.json",
]

with open(registry_path) as f:
    registry = json.load(f)

existing_names = {lane["name"] for lane in registry["lanes"]}

for config_path in config_paths:
    with open(config_path) as f:
        config = json.load(f)
    name = config["name"]
    if name in existing_names:
        print(f"ALREADY REGISTERED: {name}")
        # Update the existing lane
        for i, lane in enumerate(registry["lanes"]):
            if lane["name"] == name:
                registry["lanes"][i] = config
                print(f"  Updated lane in registry")
                break
    else:
        config["enabled"] = True
        config["watchdog_group"] = "crypto_watchdog"
        registry["lanes"].append(config)
        print(f"REGISTERED: {name} (enabled=True, watchdog=crypto_watchdog)")

with open(registry_path, "w") as f:
    json.dump(registry, f, indent=4)

print(f"Registry now has {len(registry['lanes'])} lanes")
print("DONE")
