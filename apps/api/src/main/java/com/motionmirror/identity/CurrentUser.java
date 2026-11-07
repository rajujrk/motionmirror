package com.motionmirror.identity;

import java.util.Set;
import java.util.UUID;

/** The authenticated browser user. Roles grant capabilities; ownership is still checked per resource. */
public record CurrentUser(UUID id, Set<String> roles) {

    public boolean isAdmin() {
        return roles.contains("ROLE_ADMIN");
    }
}
