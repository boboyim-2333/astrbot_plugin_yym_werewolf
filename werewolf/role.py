class Role:
    def __init__(self):
        self.name = "角色"
        self.description = "基础角色"

class Werewolf(Role):
    def __init__(self):
        super().__init__()
        self.name = "werewolf"
        self.description = "狼人"

class Seer(Role):
    def __init__(self):
        super().__init__()
        self.name = "seer"
        self.description = "预言家"

class Witch(Role):
    def __init__(self):
        super().__init__()
        self.name = "witch"
        self.description = "女巫"

class Hunter(Role):
    def __init__(self):
        super().__init__()
        self.name = "hunter"
        self.description = "猎人"

class Villager(Role):
    def __init__(self):
        super().__init__()
        self.name = "villager"
        self.description = "平民"

def get_role_by_name(name: str) -> Role:
    roles = {
        "werewolf": Werewolf,
        "seer": Seer,
        "witch": Witch,
        "hunter": Hunter,
        "villager": Villager
    }
    return roles.get(name, Role)()
