import yaml
with open('Windows/ansible/inventory.yml') as f:
    print(yaml.safe_load(f))
