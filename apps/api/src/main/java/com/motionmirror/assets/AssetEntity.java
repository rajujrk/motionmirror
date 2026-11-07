package com.motionmirror.assets;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import jakarta.persistence.Version;
import java.time.Instant;
import java.util.UUID;
import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.type.SqlTypes;

@Entity
@Table(name = "assets")
public class AssetEntity {

    public static final String DRIVING_VIDEO = "DRIVING_VIDEO";
    public static final String TARGET_REFERENCE = "TARGET_REFERENCE";
    public static final String PENDING_UPLOAD = "PENDING_UPLOAD";
    public static final String QUARANTINED = "QUARANTINED";
    public static final String READY = "READY";
    public static final String REJECTED = "REJECTED";
    public static final String DELETED = "DELETED";

    @Id
    private UUID id;

    @Column(name = "project_id", nullable = false)
    private UUID projectId;

    @Column(name = "owner_id", nullable = false)
    private UUID ownerId;

    @Column(nullable = false)
    private String role;

    @Column(nullable = false)
    private String status;

    @Column(name = "display_name", nullable = false)
    private String displayName;

    @Column(name = "declared_type", nullable = false)
    private String declaredType;

    @Column(name = "declared_bytes", nullable = false)
    private long declaredBytes;

    @Column(name = "upload_key", nullable = false)
    private String uploadKey;

    @Column(name = "object_key")
    private String objectKey;

    @Column(name = "size_bytes")
    private Long sizeBytes;

    @Column(name = "checksum_sha256")
    private String checksumSha256;

    @Column(name = "detected_type")
    private String detectedType;

    @JdbcTypeCode(SqlTypes.JSON)
    private String media;

    @Column(name = "rejection_code")
    private String rejectionCode;

    @Column(name = "rejection_detail")
    private String rejectionDetail;

    @Column(name = "upload_expires_at", nullable = false)
    private Instant uploadExpiresAt;

    @Column(name = "consent_confirmed", nullable = false)
    private boolean consentConfirmed;

    @Version
    private long version;

    @Column(name = "created_at", nullable = false)
    private Instant createdAt = Instant.now();

    @Column(name = "updated_at", nullable = false)
    private Instant updatedAt = Instant.now();

    protected AssetEntity() {}

    public AssetEntity(UUID id, UUID projectId, UUID ownerId, String role, String displayName, String declaredType,
            long declaredBytes, String uploadKey, Instant uploadExpiresAt, boolean consentConfirmed) {
        this.id = id;
        this.projectId = projectId;
        this.ownerId = ownerId;
        this.role = role;
        this.status = PENDING_UPLOAD;
        this.displayName = displayName;
        this.declaredType = declaredType;
        this.declaredBytes = declaredBytes;
        this.uploadKey = uploadKey;
        this.uploadExpiresAt = uploadExpiresAt;
        this.consentConfirmed = consentConfirmed;
    }

    public UUID getId() { return id; }
    public UUID getProjectId() { return projectId; }
    public UUID getOwnerId() { return ownerId; }
    public String getRole() { return role; }
    public String getStatus() { return status; }
    public String getDisplayName() { return displayName; }
    public String getDeclaredType() { return declaredType; }
    public long getDeclaredBytes() { return declaredBytes; }
    public String getUploadKey() { return uploadKey; }
    public String getObjectKey() { return objectKey; }
    public Long getSizeBytes() { return sizeBytes; }
    public String getChecksumSha256() { return checksumSha256; }
    public String getDetectedType() { return detectedType; }
    public String getMedia() { return media; }
    public String getRejectionCode() { return rejectionCode; }
    public String getRejectionDetail() { return rejectionDetail; }
    public Instant getUploadExpiresAt() { return uploadExpiresAt; }
    public boolean isConsentConfirmed() { return consentConfirmed; }
    public long getVersion() { return version; }
    public Instant getCreatedAt() { return createdAt; }

    /** Upload finished and the bytes are in a key no upload URL can reach. Still untrusted until validated. */
    public void quarantine(String immutableKey, long actualBytes) {
        this.objectKey = immutableKey;
        this.sizeBytes = actualBytes;
        this.status = QUARANTINED;
        this.updatedAt = Instant.now();
    }

    public void markReady(String finalKey, long actualBytes, String sha256, String detected, String mediaJson) {
        this.objectKey = finalKey;
        this.sizeBytes = actualBytes;
        this.checksumSha256 = sha256;
        this.detectedType = detected;
        this.media = mediaJson;
        this.status = READY;
        this.updatedAt = Instant.now();
    }

    public void reject(String code, String detail) {
        this.rejectionCode = code;
        this.rejectionDetail = detail;
        this.status = REJECTED;
        this.updatedAt = Instant.now();
    }

    public void markDeleted() {
        this.status = DELETED;
        this.updatedAt = Instant.now();
    }
}
