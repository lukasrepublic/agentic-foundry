workspace "ok" {
    properties {
        "model-status" "draft"
    }
    model {
        sys = softwareSystem "Shop" {
            tags "draft"
            properties {
                "source" "repo"
            }
            api = container "api" {
                tags "draft"
                properties {
                    "owner" "team"
                }
            }
        }
    }
}
