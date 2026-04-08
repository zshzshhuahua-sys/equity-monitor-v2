"""
批量导入功能
支持 CSV/Excel 格式
"""
import pandas as pd
from pathlib import Path
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass
from .validators import validator, ValidationError


@dataclass
class ImportResult:
    """导入结果"""
    total: int
    success: int
    failed: int
    updated: int
    errors: List[ValidationError]
    data: List[Dict]


class BatchImporter:
    """批量导入器"""
    
    def __init__(self):
        self.validator = validator
    
    def import_csv(self, file_path: Path) -> ImportResult:
        """导入CSV文件"""
        try:
            df = pd.read_csv(file_path, dtype=str)
            return self._process_dataframe(df)
        except Exception as e:
            return ImportResult(
                total=0,
                success=0,
                failed=0,
                updated=0,
                errors=[ValidationError(0, 'file', str(file_path), f"CSV读取失败: {e}")],
                data=[]
            )
    
    def import_excel(self, file_path: Path, sheet_name: Optional[str] = None) -> ImportResult:
        """导入Excel文件"""
        try:
            if sheet_name:
                df = pd.read_excel(file_path, sheet_name=sheet_name, dtype=str)
            else:
                df = pd.read_excel(file_path, dtype=str)
            return self._process_dataframe(df)
        except Exception as e:
            return ImportResult(
                total=0,
                success=0,
                failed=0,
                updated=0,
                errors=[ValidationError(0, 'file', str(file_path), f"Excel读取失败: {e}")],
                data=[]
            )
    
    def _process_dataframe(self, df: pd.DataFrame) -> ImportResult:
        """处理DataFrame"""
        # 标准化列名（支持中英文）
        column_mapping = {
            '股票代码': 'symbol',
            '代码': 'symbol',
            'code': 'symbol',
            '股票名称': 'name',
            '名称': 'name',
            'name': 'name',
            '执行价格': 'strike_price',
            '行权价': 'strike_price',
            '价格': 'strike_price',
            'price': 'strike_price',
            '数量': 'quantity',
            'qty': 'quantity',
            'quantity': 'quantity',
            '自定义阈值': 'custom_threshold',
            '阈值': 'custom_threshold',
            'threshold': 'custom_threshold',
        }
        
        # 重命名列
        df = df.rename(columns=lambda x: column_mapping.get(str(x).strip().lower(), str(x).strip()))
        
        errors = []
        valid_data = []
        
        for idx, row in df.iterrows():
            row_dict = row.to_dict()
            row_errors = self.validator.validate_row(row_dict, idx + 2)  # +2 因为Excel行号从1开始，还有表头
            
            if row_errors:
                errors.extend(row_errors)
            else:
                # 标准化数据
                symbol = str(row_dict.get('symbol', '')).strip()
                valid, exchange = self.validator.validate_symbol(symbol)
                
                if valid:
                    clean_data = {
                        'symbol': symbol,
                        'exchange': exchange,
                        'full_code': f"{symbol}.{exchange}",
                        'name': str(row_dict.get('name', '')).strip() or None,
                        'strike_price': float(row_dict.get('strike_price', 0)),
                        'quantity': int(float(row_dict.get('quantity', 0))) if row_dict.get('quantity') else None,
                        'custom_threshold': float(row_dict.get('custom_threshold')) if row_dict.get('custom_threshold') else None,
                    }
                    valid_data.append(clean_data)
        
        return ImportResult(
            total=len(df),
            success=len(valid_data),
            failed=len(errors),
            updated=0,  # 实际更新数需要在数据库层确定
            errors=errors,
            data=valid_data
        )
    
    def generate_error_report(self, errors: List[ValidationError]) -> pd.DataFrame:
        """生成错误报告"""
        if not errors:
            return pd.DataFrame()
        
        data = [{
            '行号': e.row,
            '字段': e.field,
            '值': e.value,
            '错误信息': e.message
        } for e in errors]
        
        return pd.DataFrame(data)


# 全局导入器实例
importer = BatchImporter()
