package com.motionmirror.assets;

import java.util.List;
import java.util.Optional;
import java.util.UUID;
import org.springframework.data.jpa.repository.JpaRepository;

public interface AssetRepository extends JpaRepository<AssetEntity, UUID> {

    Optional<AssetEntity> findByIdAndOwnerId(UUID id, UUID ownerId);

    List<AssetEntity> findByProjectIdAndStatusNotOrderByCreatedAtDesc(UUID projectId, String status);

    List<AssetEntity> findByProjectId(UUID projectId);
}
