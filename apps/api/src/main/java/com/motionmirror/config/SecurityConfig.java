package com.motionmirror.config;

import java.util.ArrayList;
import java.util.Collection;
import java.util.List;
import java.util.Map;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.core.annotation.Order;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.annotation.web.configurers.AbstractHttpConfigurer;
import org.springframework.security.config.http.SessionCreationPolicy;
import org.springframework.security.core.GrantedAuthority;
import org.springframework.security.core.authority.SimpleGrantedAuthority;
import org.springframework.security.oauth2.client.oidc.userinfo.OidcUserRequest;
import org.springframework.security.oauth2.client.oidc.userinfo.OidcUserService;
import org.springframework.security.oauth2.client.oidc.web.logout.OidcClientInitiatedLogoutSuccessHandler;
import org.springframework.security.oauth2.client.registration.ClientRegistration;
import org.springframework.security.oauth2.client.registration.ClientRegistrationRepository;
import org.springframework.security.oauth2.client.registration.InMemoryClientRegistrationRepository;
import org.springframework.security.oauth2.client.userinfo.OAuth2UserService;
import org.springframework.security.oauth2.core.AuthorizationGrantType;
import org.springframework.security.oauth2.core.ClientAuthenticationMethod;
import org.springframework.security.oauth2.core.OAuth2Error;
import org.springframework.security.oauth2.core.OAuth2TokenValidator;
import org.springframework.security.oauth2.core.OAuth2TokenValidatorResult;
import org.springframework.security.oauth2.core.oidc.user.DefaultOidcUser;
import org.springframework.security.oauth2.core.oidc.user.OidcUser;
import org.springframework.security.oauth2.jose.jws.SignatureAlgorithm;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.security.oauth2.jwt.JwtDecoder;
import org.springframework.security.oauth2.jwt.JwtIssuerValidator;
import org.springframework.security.oauth2.jwt.JwtTimestampValidator;
import org.springframework.security.oauth2.jwt.NimbusJwtDecoder;
import org.springframework.security.oauth2.core.DelegatingOAuth2TokenValidator;
import org.springframework.security.oauth2.server.resource.authentication.JwtAuthenticationToken;
import org.springframework.security.web.SecurityFilterChain;
import org.springframework.security.web.authentication.HttpStatusEntryPoint;
import org.springframework.security.web.header.writers.ReferrerPolicyHeaderWriter;
import org.springframework.web.cors.CorsConfiguration;

/** Browser and worker traffic use separate chains: session plus CSRF, and stateless bearer tokens. */
@Configuration
public class SecurityConfig {

    public static final String INTERNAL_PATHS = "/internal/**";
    private static final String WORKER_SCOPE = "SCOPE_worker";

    private final AppProperties properties;

    public SecurityConfig(AppProperties properties) {
        this.properties = properties;
    }

    /** Operators use a personal bearer token with the ADMIN role, so CLI actions are audited to a real person. */
    @Bean
    @Order(0)
    SecurityFilterChain operatorChain(HttpSecurity http) throws Exception {
        return http.securityMatcher(request -> request.getRequestURI().startsWith("/api/v1/admin/")
                        && String.valueOf(request.getHeader(HttpHeaders.AUTHORIZATION)).startsWith("Bearer "))
                .authorizeHttpRequests(auth -> auth.anyRequest().hasRole("ADMIN"))
                .oauth2ResourceServer(server -> server.jwt(jwt -> jwt
                        .decoder(operatorJwtDecoder())
                        .jwtAuthenticationConverter(SecurityConfig::operatorAuthentication)))
                .sessionManagement(session -> session.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
                .csrf(AbstractHttpConfigurer::disable)
                .build();
    }

    private JwtDecoder operatorJwtDecoder() {
        AppProperties.Oidc oidc = properties.oidc();
        NimbusJwtDecoder decoder = NimbusJwtDecoder
                .withJwkSetUri(oidc.internalBase() + "/protocol/openid-connect/certs")
                .jwsAlgorithm(SignatureAlgorithm.RS256)
                .build();
        decoder.setJwtValidator(workerTokenValidator(oidc.publicIssuer(), oidc.adminAudience()));
        return decoder;
    }

    public static JwtAuthenticationToken operatorAuthentication(Jwt token) {
        List<String> roles = token.getClaimAsStringList("roles");
        Collection<GrantedAuthority> authorities = new ArrayList<>();
        for (String role : roles == null ? List.<String>of() : roles) {
            if (role.equals("USER") || role.equals("ADMIN")) {
                authorities.add(new SimpleGrantedAuthority("ROLE_" + role));
            }
        }
        return new JwtAuthenticationToken(token, authorities, token.getSubject());
    }

    @Bean
    @Order(1)
    SecurityFilterChain workerChain(HttpSecurity http, JwtDecoder workerJwtDecoder) throws Exception {
        return http.securityMatcher(INTERNAL_PATHS)
                .authorizeHttpRequests(auth -> auth.anyRequest().hasAuthority(WORKER_SCOPE))
                .oauth2ResourceServer(server -> server.jwt(jwt -> jwt.decoder(workerJwtDecoder)))
                .sessionManagement(session -> session.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
                .csrf(AbstractHttpConfigurer::disable)
                .build();
    }

    @Bean
    @Order(2)
    SecurityFilterChain browserChain(HttpSecurity http, ClientRegistrationRepository registrations) throws Exception {
        OidcClientInitiatedLogoutSuccessHandler logoutHandler =
                new OidcClientInitiatedLogoutSuccessHandler(registrations);
        logoutHandler.setPostLogoutRedirectUri(properties.publicOrigin() + "/");

        return http.authorizeHttpRequests(auth -> auth
                        .requestMatchers("/actuator/health/**", "/actuator/info").permitAll()
                        .requestMatchers("/actuator/**").denyAll()
                        .requestMatchers("/v3/api-docs/**").access((authentication, context) ->
                                new org.springframework.security.authorization.AuthorizationDecision(!properties.isProduction()))
                        .requestMatchers("/api/v1/admin/**").hasRole("ADMIN")
                        .requestMatchers("/api/**").authenticated()
                        .anyRequest().permitAll())
                .oauth2Login(login -> login
                        .userInfoEndpoint(userInfo -> userInfo.oidcUserService(oidcUserService()))
                        .defaultSuccessUrl(properties.publicOrigin() + "/projects", true))
                .logout(logout -> logout.logoutUrl("/logout").logoutSuccessHandler(logoutHandler))
                .csrf(csrf -> csrf.spa())
                .cors(cors -> cors.configurationSource(request -> corsConfiguration()))
                .exceptionHandling(errors -> errors.defaultAuthenticationEntryPointFor(
                        new HttpStatusEntryPoint(HttpStatus.UNAUTHORIZED),
                        request -> request.getRequestURI().startsWith("/api/")))
                .sessionManagement(session -> session.sessionFixation(fixation -> fixation.changeSessionId()))
                .headers(headers -> headers
                        .frameOptions(frame -> frame.deny())
                        .contentTypeOptions(contentType -> {})
                        .referrerPolicy(referrer -> referrer.policy(
                                ReferrerPolicyHeaderWriter.ReferrerPolicy.STRICT_ORIGIN_WHEN_CROSS_ORIGIN))
                        .cacheControl(cache -> {})
                        .httpStrictTransportSecurity(hsts -> {
                            if (!properties.isProduction()) {
                                hsts.disable();
                            }
                        }))
                .build();
    }

    private CorsConfiguration corsConfiguration() {
        CorsConfiguration cors = new CorsConfiguration();
        cors.setAllowedOrigins(List.of(properties.publicOrigin()));
        cors.setAllowedMethods(List.of("GET", "POST", "PUT", "PATCH", "DELETE"));
        cors.setAllowedHeaders(List.of(HttpHeaders.CONTENT_TYPE, "X-XSRF-TOKEN", "Idempotency-Key"));
        cors.setAllowCredentials(true);
        return cors;
    }

    /** Maps the realm `roles` claim to ROLE_* authorities. */
    private OAuth2UserService<OidcUserRequest, OidcUser> oidcUserService() {
        OidcUserService delegate = new OidcUserService();
        return request -> {
            OidcUser user = delegate.loadUser(request);
            Collection<GrantedAuthority> authorities = new ArrayList<>(user.getAuthorities());
            Object roles = user.getIdToken().getClaim("roles");
            if (roles instanceof Collection<?> values) {
                values.stream()
                        .map(String::valueOf)
                        .filter(role -> role.equals("USER") || role.equals("ADMIN"))
                        .forEach(role -> authorities.add(new SimpleGrantedAuthority("ROLE_" + role)));
            }
            return new DefaultOidcUser(authorities, user.getIdToken(), user.getUserInfo(), "sub");
        };
    }

    /**
     * Explicit endpoints instead of discovery: the browser reaches the public issuer while the API reaches
     * Keycloak on the internal network. The issuer stays pinned, so the id-token check is still enforced.
     */
    @Bean
    ClientRegistrationRepository clientRegistrationRepository() {
        AppProperties.Oidc oidc = properties.oidc();
        String publicBase = oidc.publicIssuer() + "/protocol/openid-connect";
        String internalBase = oidc.internalBase() + "/protocol/openid-connect";
        ClientRegistration registration = ClientRegistration.withRegistrationId("keycloak")
                .clientId(oidc.clientId())
                .clientSecret(oidc.clientSecret())
                .clientAuthenticationMethod(ClientAuthenticationMethod.CLIENT_SECRET_BASIC)
                .authorizationGrantType(AuthorizationGrantType.AUTHORIZATION_CODE)
                .redirectUri(properties.publicOrigin() + "/login/oauth2/code/{registrationId}")
                .scope("openid", "profile", "email")
                .authorizationUri(publicBase + "/auth")
                .tokenUri(internalBase + "/token")
                .userInfoUri(internalBase + "/userinfo")
                .jwkSetUri(internalBase + "/certs")
                .userNameAttributeName("sub")
                .issuerUri(oidc.publicIssuer())
                .providerConfigurationMetadata(Map.of("end_session_endpoint", publicBase + "/logout"))
                .clientSettings(ClientRegistration.ClientSettings.builder().requireProofKey(true).build())
                .build();
        return new InMemoryClientRegistrationRepository(registration);
    }

    @Bean
    JwtDecoder workerJwtDecoder() {
        AppProperties.Oidc oidc = properties.oidc();
        NimbusJwtDecoder decoder = NimbusJwtDecoder
                .withJwkSetUri(oidc.internalBase() + "/protocol/openid-connect/certs")
                .jwsAlgorithm(SignatureAlgorithm.RS256)
                .build();
        decoder.setJwtValidator(workerTokenValidator(oidc.publicIssuer(), oidc.workerAudience()));
        return decoder;
    }

    /** Issuer, expiry and audience are all required. Exposed so tests can prove each rejection. */
    public static OAuth2TokenValidator<Jwt> workerTokenValidator(String issuer, String audience) {
        return new DelegatingOAuth2TokenValidator<>(
                new JwtTimestampValidator(),
                new JwtIssuerValidator(issuer),
                new AudienceValidator(audience));
    }

    static final class AudienceValidator implements OAuth2TokenValidator<Jwt> {
        private final String audience;

        AudienceValidator(String audience) {
            this.audience = audience;
        }

        @Override
        public OAuth2TokenValidatorResult validate(Jwt token) {
            if (token.getAudience() != null && token.getAudience().contains(audience)) {
                return OAuth2TokenValidatorResult.success();
            }
            return OAuth2TokenValidatorResult.failure(
                    new OAuth2Error("invalid_token", "Token audience is not " + audience, null));
        }
    }

}
