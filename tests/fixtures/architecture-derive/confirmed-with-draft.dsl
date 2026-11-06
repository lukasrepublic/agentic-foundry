workspace "ok" {
    properties {
        "model-status" "confirmed"
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
                    "source" "Dockerfile"
                }
            }
        }
    }
}
