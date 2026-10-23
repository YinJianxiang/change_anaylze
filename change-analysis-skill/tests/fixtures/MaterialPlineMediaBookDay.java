package com.dz.market.admin.material.model;

import java.io.Serializable;
import java.util.Date;
import java.util.List;


import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.extension.activerecord.Model;
import com.baomidou.mybatisplus.annotation.TableField;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;
import lombok.NoArgsConstructor;
import lombok.experimental.Accessors;

/**
 * 素材消耗排行榜(ad_material_pline_media_book_day)实体类
 *
 * @author machao
 * @since 2024-09-09 16:57:49
 * @description 由 Mybatisplus Code Generator 创建
 */
@Data
@NoArgsConstructor
@Accessors(chain = true)
@TableName("ad_material_pline_media_book_day")
public class MaterialPlineMediaBookDay extends Model<MaterialPlineMediaBookDay> {
    private static final long serialVersionUID = 1L;

    /**
     * id
     */
    @TableId(value = "id", type = IdType.AUTO)
    private Long id;
    /**
     * 业务线
     */
    private String plineForm;
    /**
     * 投放媒体
     */
    private String media;
    /**
     * 投放书籍id
     */
    private String bookId;
    /**
     * 媒体素材ID
     */
    private String materialId;
    /**
     * 自有素材ID
     */
    private Integer selfMaterialId;
    /**
     * 投放时间-日期
     */
    private String cdate;
    /**
     * 今日消耗
     */
    private Double consume;
    /**
     * 全域GMV
     */
    private Double allGmvTrend;
    /**
     * 混合消耗
     */
    private Double mixConsume;
    /**
     * 今日充值
     */
    private Double czMoneyDay;

    /**
     * 今日曝光数
     */
    private Integer showNumDay;
    /**
     * 今日点击数
     */
    private Integer clickNumDay;
    /**
     * 今日充值uv
     */
    private Integer czUvDay;
    /**
     * 今日点击率
     */
    private Double ctrDay;
    /**
     * 今日转化率
     */
    private Double cvrDay;
    /**
     * 今日roi
     */
    private Double roi;
    /**
     * 昨日消耗
     */
    private Double consumeYesterday;
    /**
     * 昨日充值
     */
    private Double czMoneyYesterday;
    /**
     * 昨日roi
     */
    private Double roiYesterday;
    /**
     * 近3日消耗
     */
    private Double consumeDay3;
    /**
     * 近3日充值
     */
    private Double czMoneyDay3;
    /**
     * 近3日roi
     */
    private Double roiDay3;
    /**
     * 近7日消耗
     */
    private Double consumeDay7;
    /**
     * 近7日充值
     */
    private Double czMoneyDay7;
    /**
     * 近7日roi
     */
    private Double roiDay7;
    /**
     * 近15日消耗
     */
    private Double consumeDay15;
    /**
     * 近15日充值
     */
    private Double czMoneyDay15;
    /**
     * 近15日充值uv
     */
    private Integer czUvDay15;
    /**
     * 近15日点击数
     */
    private Integer clickNumDay15;
    /**
     * 近15日曝光数
     */
    private Integer showNumDay15;
    /**
     * 近15日点击率
     */
    private Double ctrDay15;
    /**
     * 近15日转化率
     */
    private Double cvrDay15;
    /**
     * 近15日roi
     */
    private Double roiDay15;
    /**
     * 当日计划数
     */
    private Integer planNumDay;
    /**
     * 昨日计划数
     */
    private Integer planNumYesterday;
    /**
     * 当前天-2自然日消耗
     */
    private Double consumeBeforeDay2;
    /**
     * 当前天-3自然日消耗
     */
    private Double consumeBeforeDay3;
    /**
     * 当前天-4自然日消耗
     */
    private Double consumeBeforeDay4;
    /**
     * 当前天-5自然日消耗
     */
    private Double consumeBeforeDay5;
    /**
     * 当前天-6自然日消耗
     */
    private Double consumeBeforeDay6;
    /**
     * 首次同步时间
     */
    private Date ctime;
    /**
     * 信息更新时间
     */
    private Date utime;
    /**
     * 素材名称
     */
    @TableField(exist = false)
    private String materialName;
    /**
     * 是否可用
     */
    @TableField(exist = false)
    private String canUse;
    /**
     * 素材样式 横板/竖版
     */
    @TableField(exist = false)
    private String materialDirection;
    /**
     * 用户id
     */
    @TableField(exist = false)
    private Integer currentUserId;
    /**
     * 用户名
     */
    @TableField(exist = false)
    private String currentUserName;
    /**
     * 用户部门
     */
    @TableField(exist = false)
    private String currentUserDeptName;
    /**
     * 是否管理员
     */
    @TableField(exist = false)
    private boolean adminFlag;
    /**
     * 是否排除仅自己可见
     */
    @TableField(exist = false)
    private boolean excludeOnlyOwnView;
    /**
     * 是否过滤可见范围
     */
    @TableField(exist = false)
    private boolean filterViewRange = true;
    /**
     * 排行榜类型
     */
    @TableField(exist = false)
    private String rankingsType;
    /**
     * 素材url
     */
    @TableField(exist = false)
    private String materialUrl;
    /**
     * 素材封面url
     */
    @TableField(exist = false)
    private String materialCoverUrl;
    /**
     * 预览地址
     */
    @TableField(exist = false)
    private String previewUrl;
    /**
     * 审核状态
     */
    @TableField(exist = false)
    private Integer auditStatus;

    @TableField(exist = false)
    private double maxValue4Consume;

    @TableField(exist = false)
    private double consumePlanNumRate;

    @TableField(exist = false)
    private double consumePlanNumRateYesterday;

    @TableField(exist = false)
    private List<String> plineFormList;

    private String ttMaterialId;

    private String starTaskId;

    @TableField(exist = false)
    private String isStarTask; // 判断是否星广联投任务

    @TableField(exist = false)
    private Integer uploaderId;
    @TableField(exist = false)
    private String designer;
    @TableField(exist = false)
    private String rejectCondition;
    @TableField(exist = false)
    private String labelCondition;
    @TableField(exist = false)
    private String filterLabelCondition;
    @TableField(exist = false)
    private String filterMaterialName;
    @TableField(exist = false)
    private List<String> materialNameList;
    @TableField(exist = false)
    private List<String> filterMaterialNameList;
    @TableField(exist = false)
    private String ids;
    @TableField(exist = false)
    private Integer payFlag;

    @TableField(exist = false)
    private boolean joinReject;

    @TableField(exist = false)
    private String orderBy;

    @TableField(exist = false)
    private Double consumeMin;

    @TableField(exist = false)
    private Double consumeMax;

    @TableField(exist = false)
    private Double totalConsume;

    @TableField(exist = false)
    private String director;

    @Override
    public Serializable pkVal() {
        return this.id;
    }
}