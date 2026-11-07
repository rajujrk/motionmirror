package com.motionmirror.identity;

import java.util.Optional;
import java.util.UUID;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;

public interface UserRepository extends JpaRepository<UserEntity, UUID> {

    Optional<UserEntity> findByIssuerAndSubject(String issuer, String subject);

    /** Concurrent first logins for one person must converge on one row. */
    @Modifying
    @Query(value = """
            INSERT INTO users (id, issuer, subject, email, display_name)
            VALUES (:id, :issuer, :subject, :email, :displayName)
            ON CONFLICT (issuer, subject)
            DO UPDATE SET email = EXCLUDED.email, display_name = EXCLUDED.display_name
            """, nativeQuery = true)
    void upsert(UUID id, String issuer, String subject, String email, String displayName);
}
