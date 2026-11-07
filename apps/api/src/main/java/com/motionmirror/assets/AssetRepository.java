package com.motionmirror.assets;

import java.util.List;
import java.util.Optional;
import java.util.UUID;
import jakarta.persistence.LockModeType;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Lock;
import org.springframework.data.jpa.repository.Query;

public interface AssetRepository extends JpaRepository<AssetEntity, UUID> {

    Optional<AssetEntity> findByIdAndOwnerId(UUID id, UUID ownerId);

    /** Holds the row until commit, so two completions of one upload run one after the other. */
    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("SELECT a FROM AssetEntity a WHERE a.id = :id AND a.ownerId = :ownerId")
    Optional<AssetEntity> lockByIdAndOwnerId(UUID id, UUID ownerId);

    List<AssetEntity> findByProjectIdAndStatusNotOrderByCreatedAtDesc(UUID projectId, String status);

    List<AssetEntity> findByProjectId(UUID projectId);
}
