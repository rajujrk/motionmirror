package com.motionmirror.identity;

import java.util.Set;
import java.util.UUID;
import java.util.stream.Collectors;
import org.springframework.http.HttpStatus;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.GrantedAuthority;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.security.oauth2.core.oidc.user.OidcUser;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import com.motionmirror.shared.ApiException;

@Service
public class CurrentUserService {

    private final UserRepository users;

    public CurrentUserService(UserRepository users) {
        this.users = users;
    }

    @Transactional
    public CurrentUser require() {
        Authentication authentication = SecurityContextHolder.getContext().getAuthentication();
        if (authentication == null || !(authentication.getPrincipal() instanceof OidcUser oidc)) {
            throw new ApiException(HttpStatus.UNAUTHORIZED, "UNAUTHENTICATED", "Sign in to continue");
        }
        Set<String> roles = authentication.getAuthorities().stream()
                .map(GrantedAuthority::getAuthority)
                .collect(Collectors.toUnmodifiableSet());
        return new CurrentUser(resolve(oidc).getId(), roles);
    }

    /** Identity is issuer plus subject. Email is profile data and may change. */
    @Transactional
    public UserEntity resolve(OidcUser oidc) {
        String issuer = String.valueOf(oidc.getIssuer());
        String subject = oidc.getSubject();
        users.upsert(UUID.randomUUID(), issuer, subject, oidc.getEmail(), oidc.getFullName());
        return users.findByIssuerAndSubject(issuer, subject).orElseThrow();
    }
}
