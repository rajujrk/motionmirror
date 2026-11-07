package com.motionmirror.identity;

import java.util.Set;
import java.util.UUID;
import java.util.stream.Collectors;
import org.springframework.http.HttpStatus;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.GrantedAuthority;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.security.oauth2.core.oidc.user.OidcUser;
import org.springframework.security.oauth2.jwt.Jwt;
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
        if (authentication == null) {
            throw new ApiException(HttpStatus.UNAUTHORIZED, "UNAUTHENTICATED", "Sign in to continue");
        }
        Set<String> roles = authentication.getAuthorities().stream()
                .map(GrantedAuthority::getAuthority)
                .collect(Collectors.toUnmodifiableSet());
        if (authentication.getPrincipal() instanceof OidcUser oidc) {
            return new CurrentUser(resolve(oidc).getId(), roles);
        }
        if (authentication.getPrincipal() instanceof Jwt token) {
            return new CurrentUser(resolve(String.valueOf(token.getIssuer()), token.getSubject(),
                    token.getClaimAsString("email"), token.getClaimAsString("name")).getId(), roles);
        }
        throw new ApiException(HttpStatus.UNAUTHORIZED, "UNAUTHENTICATED", "Sign in to continue");
    }

    /** Identity is issuer plus subject. Email is profile data and may change. */
    @Transactional
    public UserEntity resolve(OidcUser oidc) {
        return resolve(String.valueOf(oidc.getIssuer()), oidc.getSubject(), oidc.getEmail(), oidc.getFullName());
    }

    private UserEntity resolve(String issuer, String subject, String email, String name) {
        users.upsert(UUID.randomUUID(), issuer, subject, email, name);
        return users.findByIssuerAndSubject(issuer, subject).orElseThrow();
    }
}
