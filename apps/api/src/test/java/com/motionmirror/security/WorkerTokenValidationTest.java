package com.motionmirror.security;

import static org.assertj.core.api.Assertions.assertThat;

import com.motionmirror.config.SecurityConfig;
import java.time.Instant;
import java.util.List;
import org.junit.jupiter.api.Test;
import org.springframework.security.oauth2.core.OAuth2TokenValidator;
import org.springframework.security.oauth2.jwt.Jwt;

class WorkerTokenValidationTest {

    private static final String ISSUER = "https://id.test/realms/motionmirror";
    private static final String AUDIENCE = "motionmirror-internal";

    private final OAuth2TokenValidator<Jwt> validator = SecurityConfig.workerTokenValidator(ISSUER, AUDIENCE);

    private static Jwt token(String issuer, String audience, Instant expires) {
        return Jwt.withTokenValue("token").header("alg", "RS256").issuer(issuer).audience(List.of(audience))
                .issuedAt(expires.minusSeconds(600)).expiresAt(expires).build();
    }

    @Test
    void acceptsAValidToken() {
        assertThat(validator.validate(token(ISSUER, AUDIENCE, Instant.now().plusSeconds(300))).hasErrors()).isFalse();
    }

    @Test
    void rejectsTheWrongIssuer() {
        assertThat(validator.validate(token("https://evil.test", AUDIENCE, Instant.now().plusSeconds(300))).hasErrors()).isTrue();
    }

    @Test
    void rejectsTheWrongAudience() {
        assertThat(validator.validate(token(ISSUER, "account", Instant.now().plusSeconds(300))).hasErrors()).isTrue();
    }

    @Test
    void rejectsAnExpiredToken() {
        assertThat(validator.validate(token(ISSUER, AUDIENCE, Instant.now().minusSeconds(600))).hasErrors()).isTrue();
    }
}
