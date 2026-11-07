package com.motionmirror.identity;

import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.core.oidc.user.OidcUser;
import org.springframework.security.web.csrf.CsrfToken;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import java.util.List;
import java.util.UUID;
import jakarta.servlet.http.HttpServletRequest;

@RestController
@RequestMapping("/api/v1/auth")
public class AuthController {

    public record Me(UUID id, String email, String displayName, List<String> roles) {}

    public record Csrf(String headerName, String token) {}

    private final CurrentUserService currentUser;

    public AuthController(CurrentUserService currentUser) {
        this.currentUser = currentUser;
    }

    @GetMapping("/me")
    public Me me(@AuthenticationPrincipal OidcUser oidc) {
        CurrentUser user = currentUser.require();
        UserEntity entity = currentUser.resolve(oidc);
        return new Me(user.id(), entity.getEmail(), entity.getDisplayName(),
                user.roles().stream().filter(role -> role.startsWith("ROLE_")).sorted().toList());
    }

    /** Lets the browser fetch a CSRF token explicitly before its first mutation. */
    @GetMapping("/csrf")
    public Csrf csrf(HttpServletRequest request) {
        CsrfToken token = (CsrfToken) request.getAttribute(CsrfToken.class.getName());
        return new Csrf(token.getHeaderName(), token.getToken());
    }
}
