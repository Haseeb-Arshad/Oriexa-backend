import hashlib

keys = {
    "freelancer": "th_agent_6d25839eedb71d5d2bd5a0956e04fd803d1c7e540bcect0abe11b0d7cbfd64ca8",
    "reviewer": "th_agent_5c10e84836a44cffaa85fb184327e93ace543eb4bc08b75a2d97b9b9a2d3e985"
}

for name, key in keys.items():
    h = hashlib.sha256(key.encode()).hexdigest()
    print(f"{name} hash: {h}")
